// SPDX-License-Identifier: GPL-2.0-only
// Copyright (c) 2026 Šerif Rami <ramiserifpersia@gmail.com>

#include <linux/init.h>
#include <linux/module.h>
#include <linux/usb.h>
#include <linux/slab.h>
#include <sound/core.h>
#include <sound/initval.h>
#include "us1800.h"

MODULE_AUTHOR("Šerif Rami <ramiserifpersia@gmail.com>");
MODULE_DESCRIPTION("ALSA Driver for TASCAM US-1800");
MODULE_LICENSE("GPL");

static int index[SNDRV_CARDS] = SNDRV_DEFAULT_IDX;
static char *id[SNDRV_CARDS] = SNDRV_DEFAULT_STR;
static bool enable[SNDRV_CARDS] = { 1, [1 ...(SNDRV_CARDS - 1)] = 0 };
static atomic_t dev_idx = ATOMIC_INIT(0);

static int us1800_probe(struct usb_interface *intf, const struct usb_device_id *usb_id);
static void us1800_disconnect(struct usb_interface *intf);
static int us1800_suspend(struct usb_interface *intf, pm_message_t message);
static int us1800_resume(struct usb_interface *intf);

void us1800_free_urbs(struct us1800_card *us1800)
{
	int i;

	usb_kill_anchored_urbs(&us1800->playback_anchor);
	usb_kill_anchored_urbs(&us1800->feedback_anchor);
	usb_kill_anchored_urbs(&us1800->capture_anchor);

	for (i = 0; i < NUM_PLAYBACK_URBS; i++) {
		if (us1800->playback_urbs[i]) {
			usb_free_coherent(us1800->dev, us1800->playback_urb_alloc_size,
							  us1800->playback_urbs[i]->transfer_buffer,
					 us1800->playback_urbs[i]->transfer_dma);
			usb_free_urb(us1800->playback_urbs[i]);
			us1800->playback_urbs[i] = NULL;
		}
	}

	for (i = 0; i < NUM_FEEDBACK_URBS; i++) {
		if (us1800->feedback_urbs[i]) {
			usb_free_coherent(us1800->dev, us1800->feedback_urb_alloc_size,
							  us1800->feedback_urbs[i]->transfer_buffer,
					 us1800->feedback_urbs[i]->transfer_dma);
			usb_free_urb(us1800->feedback_urbs[i]);
			us1800->feedback_urbs[i] = NULL;
		}
	}

	for (i = 0; i < NUM_CAPTURE_URBS; i++) {
		if (us1800->capture_urbs[i]) {
			usb_free_coherent(us1800->dev, CAPTURE_PACKET_SIZE,
							  us1800->capture_urbs[i]->transfer_buffer,
					 us1800->capture_urbs[i]->transfer_dma);
			usb_free_urb(us1800->capture_urbs[i]);
			us1800->capture_urbs[i] = NULL;
		}
	}
}

int us1800_alloc_urbs(struct us1800_card *us1800)
{
	int i;

	us1800->playback_urb_alloc_size = PLAYBACK_URB_PACKETS * 156;

	for (i = 0; i < NUM_PLAYBACK_URBS; i++) {
		struct urb *urb = usb_alloc_urb(PLAYBACK_URB_PACKETS, GFP_KERNEL);

		if (!urb)
			return -ENOMEM;
		us1800->playback_urbs[i] = urb;
		urb->transfer_buffer = usb_alloc_coherent(us1800->dev, us1800->playback_urb_alloc_size,
												  GFP_KERNEL, &urb->transfer_dma);
		if (!urb->transfer_buffer)
			return -ENOMEM;

		urb->dev = us1800->dev;
		urb->pipe = usb_sndisocpipe(us1800->dev, EP_AUDIO_OUT);
		urb->transfer_flags = URB_NO_TRANSFER_DMA_MAP;
		urb->interval = 1;
		urb->complete = playback_urb_complete;
		urb->context = us1800;
	}

	us1800->feedback_urb_alloc_size = FEEDBACK_URB_PACKETS * FEEDBACK_PACKET_SIZE;

	for (i = 0; i < NUM_FEEDBACK_URBS; i++) {
		struct urb *urb = usb_alloc_urb(FEEDBACK_URB_PACKETS, GFP_KERNEL);

		if (!urb)
			return -ENOMEM;
		us1800->feedback_urbs[i] = urb;
		urb->transfer_buffer = usb_alloc_coherent(us1800->dev, us1800->feedback_urb_alloc_size,
												  GFP_KERNEL, &urb->transfer_dma);
		if (!urb->transfer_buffer)
			return -ENOMEM;
		urb->dev = us1800->dev;
		urb->pipe = usb_rcvisocpipe(us1800->dev, EP_PLAYBACK_FEEDBACK);
		urb->transfer_flags = URB_ISO_ASAP | URB_NO_TRANSFER_DMA_MAP;
		urb->interval = 4;
		urb->context = us1800;
		urb->complete = feedback_urb_complete;
	}

	for (i = 0; i < NUM_CAPTURE_URBS; i++) {
		struct urb *urb = usb_alloc_urb(0, GFP_KERNEL);
		void *buf;

		if (!urb)
			return -ENOMEM;
		us1800->capture_urbs[i] = urb;
		buf = usb_alloc_coherent(us1800->dev, CAPTURE_PACKET_SIZE,
								 GFP_KERNEL, &urb->transfer_dma);
		if (!buf)
			return -ENOMEM;
		usb_fill_bulk_urb(urb, us1800->dev,
						  usb_rcvbulkpipe(us1800->dev, EP_AUDIO_IN),
						  buf, CAPTURE_PACKET_SIZE,
					capture_urb_complete, us1800);
		urb->transfer_flags |= URB_NO_TRANSFER_DMA_MAP;
	}

	return 0;
}

void us1800_stop_work_handler(struct work_struct *work)
{
	struct us1800_card *us1800 = container_of(work, struct us1800_card, stop_work);

	usb_kill_anchored_urbs(&us1800->playback_anchor);
	usb_kill_anchored_urbs(&us1800->feedback_anchor);
	usb_kill_anchored_urbs(&us1800->capture_anchor);

	if (!atomic_read(&us1800->playback_active) && !atomic_read(&us1800->capture_active)) {
		usb_control_msg(us1800->dev, usb_sndctrlpipe(us1800->dev, 0),
						VENDOR_REQ_MODE_CONTROL, RT_H2D_VENDOR_DEV,
				  MODE_VAL_STREAM_STOP_US1800, 0x0000, NULL, 0, USB_CTRL_TIMEOUT_MS);
	}
}

static void us1800_card_private_free(struct snd_card *card)
{
	struct us1800_card *us1800 = card->private_data;

	if (us1800) {
		us1800_free_urbs(us1800);
		if (us1800->dev) {
			usb_put_dev(us1800->dev);
			us1800->dev = NULL;
		}
	}
}

static int us1800_probe(struct usb_interface *intf, const struct usb_device_id *usb_id)
{
	struct usb_device *dev = interface_to_usbdev(intf);
	struct snd_card *card;
	struct us1800_card *us1800;
	int err;
	int idx;

	if (intf->cur_altsetting->desc.bInterfaceNumber == 1)
		return 0;

	idx = atomic_fetch_inc(&dev_idx);
	if (idx >= SNDRV_CARDS) {
		atomic_dec(&dev_idx);
		return -ENODEV;
	}
	if (!enable[idx]) {
		atomic_dec(&dev_idx);
		return -ENOENT;
	}

	dev_info(&dev->dev, "TASCAM US-1800 driver probe started.\n");

	err = snd_card_new(&dev->dev, index[idx], id[idx], THIS_MODULE,
					   sizeof(struct us1800_card), &card);
	if (err < 0) {
		atomic_dec(&dev_idx);
		return err;
	}

	us1800 = card->private_data;
	card->private_free = us1800_card_private_free;
	us1800->dev = usb_get_dev(dev);
	us1800->card = card;
	us1800->iface0 = intf;

	spin_lock_init(&us1800->lock);
	init_usb_anchor(&us1800->playback_anchor);
	init_usb_anchor(&us1800->feedback_anchor);
	init_usb_anchor(&us1800->capture_anchor);

	INIT_WORK(&us1800->stop_work, us1800_stop_work_handler);
	INIT_WORK(&us1800->stop_pcm_work, us1800_stop_pcm_work_handler);

	strscpy(card->driver, DRIVER_NAME, sizeof(card->driver));
	strscpy(card->shortname, "US-1800", sizeof(card->shortname));
	snprintf(card->longname, sizeof(card->longname), "%s (%04x:%04x) at %s",
			 card->shortname, USB_VID_TASCAM, dev->descriptor.idProduct,
		  dev_name(&dev->dev));

	/* Allocate PCM Playback & Capture device */
	err = snd_pcm_new(card, "US1800 PCM", 0, 1, 1, &us1800->pcm);
	if (err < 0)
		goto free_card;
	us1800->pcm->private_data = us1800;
	strscpy(us1800->pcm->name, "US1800 PCM", sizeof(us1800->pcm->name));
	snd_pcm_set_ops(us1800->pcm, SNDRV_PCM_STREAM_PLAYBACK, &us1800_playback_ops);
	snd_pcm_set_ops(us1800->pcm, SNDRV_PCM_STREAM_CAPTURE, &us1800_capture_ops);
	snd_pcm_set_managed_buffer_all(us1800->pcm, SNDRV_DMA_TYPE_VMALLOC, NULL, 0, 0);

	us1800->scratch_buf = devm_kzalloc(&dev->dev, 16, GFP_KERNEL);
	if (!us1800->scratch_buf) {
		err = -ENOMEM;
		goto free_card;
	}

	dev_info(&dev->dev, "Querying firmware revisions...\n");
	err = usb_control_msg(dev, usb_rcvctrlpipe(dev, 0),
						  VENDOR_REQ_FIRMWARE_READ, RT_D2H_VENDOR_DEV,
					   0x0000, 0x0000, us1800->scratch_buf, 15,
					   USB_CTRL_TIMEOUT_MS);
	if (err < 0) {
		dev_err(&dev->dev, "Firmware lookup failed: %d\n", err);
		goto free_card;
	}
	dev_info(&dev->dev, "Firmware reports version string: %s\n", us1800->scratch_buf + 4);

	err = usb_set_interface(dev, 0, 1);
	if (err < 0) {
		dev_err(&dev->dev, "Alternate setting selection for Interface 0 failed: %d\n", err);
		goto free_card;
	}

	err = usb_set_interface(dev, 1, 1);
	if (err < 0) {
		dev_err(&dev->dev, "Alternate setting selection for Interface 1 failed: %d\n", err);
		goto free_card;
	}

	dev_info(&dev->dev, "Reading initial boot state...\n");
	err = usb_control_msg(dev, usb_rcvctrlpipe(dev, 0),
						  VENDOR_REQ_MODE_CONTROL, RT_D2H_VENDOR_DEV,
					   MODE_VAL_HANDSHAKE_READ, 0x0000, us1800->scratch_buf, 1,
					   USB_CTRL_TIMEOUT_MS);
	if (err < 0) {
		dev_err(&dev->dev, "Boot state verification failed: %d\n", err);
		goto free_card;
	}
	dev_info(&dev->dev, "Boot state handshake: 0x%02x\n", us1800->scratch_buf[0]);

	err = us1800_alloc_urbs(us1800);
	if (err < 0)
		goto free_card;

	err = us1800_configure_device_for_rate(us1800, 48000);
	if (err < 0) {
		dev_err(&dev->dev, "Initial target clock initialization failed.\n");
		goto free_card;
	}
	us1800->current_rate = 48000;

	err = snd_card_register(card);
	if (err < 0)
		goto free_card;

	usb_set_intfdata(intf, us1800);
	dev_info(&dev->dev, "TASCAM US-1800 card probed and registered successfully.\n");
	return 0;

	free_card:
	snd_card_free(card);
	atomic_dec(&dev_idx);
	return err;
}

static void us1800_disconnect(struct usb_interface *intf)
{
	struct us1800_card *us1800 = usb_get_intfdata(intf);

	if (!us1800)
		return;

	if (intf->cur_altsetting->desc.bInterfaceNumber == 0) {
		dev_info(&us1800->dev->dev, "Removing TASCAM US-1800 from system.\n");
		atomic_set(&us1800->playback_active, 0);
		atomic_set(&us1800->capture_active, 0);

		usb_kill_anchored_urbs(&us1800->playback_anchor);
		usb_kill_anchored_urbs(&us1800->feedback_anchor);
		usb_kill_anchored_urbs(&us1800->capture_anchor);

		snd_card_disconnect(us1800->card);
		cancel_work_sync(&us1800->stop_work);
		cancel_work_sync(&us1800->stop_pcm_work);
		usb_set_intfdata(intf, NULL);
		snd_card_free(us1800->card);
		atomic_dec(&dev_idx);
	}
}

static int us1800_suspend(struct usb_interface *intf, pm_message_t message)
{
	struct us1800_card *us1800 = usb_get_intfdata(intf);

	if (!us1800)
		return 0;

	snd_pcm_suspend_all(us1800->pcm);
	cancel_work_sync(&us1800->stop_work);
	cancel_work_sync(&us1800->stop_pcm_work);

	usb_kill_anchored_urbs(&us1800->playback_anchor);
	usb_kill_anchored_urbs(&us1800->feedback_anchor);
	usb_kill_anchored_urbs(&us1800->capture_anchor);

	usb_control_msg(us1800->dev, usb_sndctrlpipe(us1800->dev, 0),
					VENDOR_REQ_POWER_CONTROL, RT_H2D_VENDOR_DEV,
				 MODE_VAL_DEEP_SLEEP, 0x0000, NULL, 0, USB_CTRL_TIMEOUT_MS);

	return 0;
}

static int us1800_resume(struct usb_interface *intf)
{
	struct us1800_card *us1800 = usb_get_intfdata(intf);
	int err;
	unsigned long flags;
	int current_rate;

	if (!us1800)
		return 0;

	usb_control_msg(us1800->dev, usb_sndctrlpipe(us1800->dev, 0),
					VENDOR_REQ_MODE_CONTROL, RT_H2D_VENDOR_DEV,
				 MODE_VAL_WAKE_UP, 0x0000, NULL, 0, USB_CTRL_TIMEOUT_MS);

	err = usb_set_interface(us1800->dev, 0, 1);
	if (err < 0) {
		dev_err(&us1800->dev->dev, "Resume: failed to reset interface 0: %d\n", err);
		return err;
	}

	err = usb_set_interface(us1800->dev, 1, 1);
	if (err < 0) {
		dev_err(&us1800->dev->dev, "Resume: failed to reset interface 1: %d\n", err);
		return err;
	}

	spin_lock_irqsave(&us1800->lock, flags);
	current_rate = us1800->current_rate;
	spin_unlock_irqrestore(&us1800->lock, flags);

	if (current_rate > 0) {
		err = us1800_configure_device_for_rate(us1800, current_rate);
		if (err < 0)
			dev_err(&us1800->dev->dev, "Resume: rate re-configuration failed: %d\n", err);
	}

	return 0;
}

static const struct usb_device_id us1800_usb_ids[] = {
	{ USB_DEVICE(USB_VID_TASCAM, USB_PID_TASCAM_US1800) },
	{ }
};
MODULE_DEVICE_TABLE(usb, us1800_usb_ids);

static struct usb_driver us1800_alsa_driver = {
	.name = DRIVER_NAME,
	.probe = us1800_probe,
	.disconnect = us1800_disconnect,
	.suspend = us1800_suspend,
	.resume = us1800_resume,
	.reset_resume = us1800_resume,
	.id_table = us1800_usb_ids,
};

module_usb_driver(us1800_alsa_driver);
