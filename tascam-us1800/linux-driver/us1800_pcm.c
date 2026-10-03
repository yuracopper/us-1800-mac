// SPDX-License-Identifier: GPL-2.0-only
// Copyright (c) 2026 Šerif Rami <ramiserifpersia@gmail.com>

#include "us1800_pcm.h"

static int us1800_write_regs_local(struct us1800_card *us1800, const u16 *regs, size_t count)
{
	int i, err = 0;
	struct usb_device *dev = us1800->dev;

	for (i = 0; i < count; i++) {
		err = usb_control_msg(dev, usb_sndctrlpipe(dev, 0),
							  VENDOR_REQ_REGISTER_WRITE, RT_H2D_VENDOR_DEV,
						regs[i], REG_VAL_ENABLE, NULL, 0, USB_CTRL_TIMEOUT_MS);
		if (err < 0)
			return err;
	}
	return 0;
}

int us1800_configure_device_for_rate(struct us1800_card *us1800, int rate)
{
	struct usb_device *dev = us1800->dev;
	u8 *rate_payload;
	int err = 0;
	const u8 *current_payload_src;
	u16 rate_reg;
	static const u8 payload_44100[] = { 0x44, 0xac, 0x00 };
	static const u8 payload_48000[] = { 0x80, 0xbb, 0x00 };
	static const u8 payload_88200[] = { 0x88, 0x58, 0x01 };
	static const u8 payload_96000[] = { 0x00, 0x77, 0x01 };

	if (!dev)
		return -ENODEV;

	switch (rate) {
		case 44100:
			current_payload_src = payload_44100;
			rate_reg = REG_ADDR_RATE_44100;
			break;
		case 48000:
			current_payload_src = payload_48000;
			rate_reg = REG_ADDR_RATE_48000;
			break;
		case 88200:
			current_payload_src = payload_88200;
			rate_reg = REG_ADDR_RATE_88200;
			break;
		case 96000:
			current_payload_src = payload_96000;
			rate_reg = REG_ADDR_RATE_96000;
			break;
		default:
			return -EINVAL;
	}

	rate_payload = kmemdup(current_payload_src, 3, GFP_KERNEL);
	if (!rate_payload)
		return -ENOMEM;

	err = usb_control_msg(dev, usb_sndctrlpipe(dev, 0),
						  VENDOR_REQ_MODE_CONTROL, RT_H2D_VENDOR_DEV,
					   MODE_VAL_CONFIG, 0x0002, NULL, 0, USB_CTRL_TIMEOUT_MS);
	if (err < 0)
		goto out;

	err = usb_control_msg(dev, usb_sndctrlpipe(dev, 0), UAC_SET_CUR,
						  RT_H2D_CLASS_EP, UAC_SAMPLING_FREQ_CONTROL,
					   EP_AUDIO_IN, rate_payload, 3, USB_CTRL_TIMEOUT_MS);
	if (err < 0)
		goto out;

	err = usb_control_msg(dev, usb_sndctrlpipe(dev, 0), UAC_SET_CUR,
						  RT_H2D_CLASS_EP, UAC_SAMPLING_FREQ_CONTROL,
					   EP_AUDIO_OUT, rate_payload, 3, USB_CTRL_TIMEOUT_MS);
	if (err < 0)
		goto out;

	err = usb_control_msg(dev, usb_rcvctrlpipe(dev, 0), VENDOR_REQ_REGISTER_WRITE,
						  RT_D2H_VENDOR_DEV, 0x0d00, REG_VAL_ENABLE, us1800->scratch_buf,
					   5, USB_CTRL_TIMEOUT_MS);
	if (err < 0)
		goto out;

	{
		const u16 regs_to_write[] = {
			REG_ADDR_INIT_0D, REG_ADDR_INIT_0E,
			REG_ADDR_INIT_0F, rate_reg, REG_ADDR_INIT_11
		};
		err = us1800_write_regs_local(us1800, regs_to_write, ARRAY_SIZE(regs_to_write));
		if (err < 0)
			goto out;
	}

	err = usb_control_msg(dev, usb_rcvctrlpipe(dev, 0), UAC_GET_CUR,
						  RT_D2H_CLASS_EP, UAC_SAMPLING_FREQ_CONTROL,
					   EP_AUDIO_IN, us1800->scratch_buf, 3, USB_CTRL_TIMEOUT_MS);
	if (err < 0)
		goto out;

	err = usb_control_msg(dev, usb_rcvctrlpipe(dev, 0), VENDOR_REQ_MODE_CONTROL,
						  RT_D2H_VENDOR_DEV, MODE_VAL_HANDSHAKE_READ, 0x0000,
						  us1800->scratch_buf, 1, USB_CTRL_TIMEOUT_MS);
	if (err < 0)
		goto out;

	err = usb_control_msg(dev, usb_sndctrlpipe(dev, 0),
						  VENDOR_REQ_MODE_CONTROL, RT_H2D_VENDOR_DEV,
						  MODE_VAL_STREAM_START_US1800, 0x0000, NULL, 0, USB_CTRL_TIMEOUT_MS);

	out:
	kfree(rate_payload);
	return err;
}

int us1800_pcm_hw_params(struct snd_pcm_substream *substream,
						 struct snd_pcm_hw_params *params)
{
	struct us1800_card *us1800 = snd_pcm_substream_chip(substream);
	unsigned int rate = params_rate(params);
	int err;
	unsigned long flags;

	spin_lock_irqsave(&us1800->lock, flags);
	if (us1800->current_rate == rate) {
		spin_unlock_irqrestore(&us1800->lock, flags);
		return 0;
	}

	if (atomic_read(&us1800->playback_active) ||
		atomic_read(&us1800->capture_active)) {
		spin_unlock_irqrestore(&us1800->lock, flags);
		return -EBUSY;
	}

	us1800->rate_changing = true;
	spin_unlock_irqrestore(&us1800->lock, flags);

	usb_kill_anchored_urbs(&us1800->playback_anchor);
	usb_kill_anchored_urbs(&us1800->feedback_anchor);
	usb_kill_anchored_urbs(&us1800->capture_anchor);

	atomic_set(&us1800->active_urbs, 0);

	err = us1800_configure_device_for_rate(us1800, rate);

	spin_lock_irqsave(&us1800->lock, flags);
	us1800->rate_changing = false;
	if (err < 0)
		us1800->current_rate = 0;
	else
		us1800->current_rate = rate;
	spin_unlock_irqrestore(&us1800->lock, flags);

	return err;
}

void us1800_stop_pcm_work_handler(struct work_struct *work)
{
	struct us1800_card *us1800 = container_of(work, struct us1800_card, stop_pcm_work);

	if (!us1800->dev)
		return;

	if (us1800->playback_substream)
		snd_pcm_stop(us1800->playback_substream, SNDRV_PCM_STATE_XRUN);
	if (us1800->capture_substream)
		snd_pcm_stop(us1800->capture_substream, SNDRV_PCM_STATE_XRUN);
}
