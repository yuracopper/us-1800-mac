// SPDX-License-Identifier: GPL-2.0-only
// Copyright (c) 2026 Šerif Rami <ramiserifpersia@gmail.com>

#include <linux/version.h>
#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 12, 0)
#include <linux/unaligned.h>
#else
#include <asm/unaligned.h>
#endif

#include "us1800_pcm.h"

const struct snd_pcm_hardware us1800_capture_hw = {
	.info = (SNDRV_PCM_INFO_MMAP | SNDRV_PCM_INFO_INTERLEAVED |
		 SNDRV_PCM_INFO_BLOCK_TRANSFER | SNDRV_PCM_INFO_MMAP_VALID |
		 SNDRV_PCM_INFO_PAUSE | SNDRV_PCM_INFO_RESUME),
	.formats = SNDRV_PCM_FMTBIT_S32_LE,
	.rates = (SNDRV_PCM_RATE_44100 | SNDRV_PCM_RATE_48000 |
		  SNDRV_PCM_RATE_88200 | SNDRV_PCM_RATE_96000),
	.rate_min = 44100,
	.rate_max = 96000,
	.channels_min = CAPTURE_CHANNELS,
	.channels_max = CAPTURE_CHANNELS,
	.buffer_bytes_max = 1024 * 1024,
	.period_bytes_min = 768,
	.period_bytes_max = 1024 * 1024,
	.periods_min = 2,
	.periods_max = 1024,
};

static int us1800_capture_open(struct snd_pcm_substream *substream)
{
	struct us1800_card *us1800 = snd_pcm_substream_chip(substream);

	substream->runtime->hw = us1800_capture_hw;
	us1800->capture_substream = substream;
	atomic_set(&us1800->capture_active, 0);
	return 0;
}

static int us1800_capture_close(struct snd_pcm_substream *substream)
{
	struct us1800_card *us1800 = snd_pcm_substream_chip(substream);

	atomic_set(&us1800->capture_active, 0);
	usb_kill_anchored_urbs(&us1800->capture_anchor);
	us1800->capture_substream = NULL;
	return 0;
}

static int us1800_capture_prepare(struct snd_pcm_substream *substream)
{
	struct us1800_card *us1800 = snd_pcm_substream_chip(substream);

	usb_kill_anchored_urbs(&us1800->capture_anchor);

	us1800->driver_capture_pos = 0;
	us1800->capture_frames_processed = 0;
	us1800->last_cap_period_pos = 0;
	return 0;
}

static snd_pcm_uframes_t us1800_capture_pointer(struct snd_pcm_substream *substream)
{
	struct us1800_card *us1800 = snd_pcm_substream_chip(substream);
	unsigned long flags;
	u64 pos;
	snd_pcm_uframes_t buffer_size = substream->runtime->buffer_size;

	if (!atomic_read(&us1800->capture_active))
		return 0;
	spin_lock_irqsave(&us1800->lock, flags);
	pos = us1800->capture_frames_processed;
	spin_unlock_irqrestore(&us1800->lock, flags);

	return (snd_pcm_uframes_t)(pos % buffer_size);
}

static int us1800_capture_trigger(struct snd_pcm_substream *substream, int cmd)
{
	struct us1800_card *us1800 = snd_pcm_substream_chip(substream);
	int i, ret = 0;
	bool start = false;
	bool stop = false;
	unsigned long flags;

	spin_lock_irqsave(&us1800->lock, flags);
	switch (cmd) {
		case SNDRV_PCM_TRIGGER_START:
		case SNDRV_PCM_TRIGGER_RESUME:
		case SNDRV_PCM_TRIGGER_PAUSE_RELEASE:
			if (us1800->rate_changing) {
				ret = -EBUSY;
				break;
			}
			if (!atomic_read(&us1800->capture_active)) {
				atomic_set(&us1800->capture_active, 1);
				start = true;
			}
			break;
		case SNDRV_PCM_TRIGGER_STOP:
		case SNDRV_PCM_TRIGGER_SUSPEND:
		case SNDRV_PCM_TRIGGER_PAUSE_PUSH:
			atomic_set(&us1800->capture_active, 0);
			stop = true;
			break;
		default:
			ret = -EINVAL;
			break;
	}
	spin_unlock_irqrestore(&us1800->lock, flags);

	if (stop) {
		smp_mb();
		for (i = 0; i < NUM_CAPTURE_URBS; i++) {
			if (us1800->capture_urbs[i])
				usb_unlink_urb(us1800->capture_urbs[i]);
		}

		if (!atomic_read(&us1800->playback_active)) {
			schedule_work(&us1800->stop_work);
		}
	}

	if (start) {
		for (i = 0; i < NUM_CAPTURE_URBS; i++) {
			usb_anchor_urb(us1800->capture_urbs[i], &us1800->capture_anchor);
			if (usb_submit_urb(us1800->capture_urbs[i], GFP_ATOMIC) < 0) {
				usb_unanchor_urb(us1800->capture_urbs[i]);
				atomic_set(&us1800->capture_active, 0);
				smp_mb();
				for (int j = 0; j < i; j++)
					usb_unlink_urb(us1800->capture_urbs[j]);
				ret = -EIO;
				break;
			}
			atomic_inc(&us1800->active_urbs);
		}
	}
	return ret;
}

static void us1800_decode_capture_chunk(const u8 *src, u32 *dst, int frames_to_decode)
{
	int f, i, k;

	for (f = 0; f < frames_to_decode; f++) {
		const u8 *src_even = src + (f * 64);
		const u8 *src_odd = src + (f * 64) + 32;
		u32 ch[16] = {0};

		/* Decode even channels from first 32-byte block */
		for (i = 0; i < 24; i++) {
			u8 v14 = src_even[i];
			ch[0]  = (ch[0] << 1)  | (v14 & 0x01);
			ch[2]  = (ch[2] << 1)  | ((v14 >> 1) & 0x01);
			ch[4]  = (ch[4] << 1)  | ((v14 >> 2) & 0x01);
			ch[6]  = (ch[6] << 1)  | ((v14 >> 3) & 0x01);
			ch[8]  = (ch[8] << 1)  | ((v14 >> 4) & 0x01);
			ch[10] = (ch[10] << 1) | ((v14 >> 5) & 0x01);
			ch[12] = (ch[12] << 1) | ((v14 >> 6) & 0x01);
			ch[14] = (ch[14] << 1) | ((v14 >> 7) & 0x01);
		}

		/* Decode odd channels from second 32-byte block */
		for (i = 0; i < 24; i++) {
			u8 v24 = src_odd[i];
			ch[1]  = (ch[1] << 1)  | (v24 & 0x01);
			ch[3]  = (ch[3] << 1)  | ((v24 >> 1) & 0x01);
			ch[5]  = (ch[5] << 1)  | ((v24 >> 2) & 0x01);
			ch[7]  = (ch[7] << 1)  | ((v24 >> 3) & 0x01);
			ch[9]  = (ch[9] << 1)  | ((v24 >> 4) & 0x01);
			ch[11] = (ch[11] << 1) | ((v24 >> 5) & 0x01);
			ch[13] = (ch[13] << 1) | ((v24 >> 6) & 0x01);
			ch[15] = (ch[15] << 1) | ((v24 >> 7) & 0x01);
		}

		/* Shift left by 8 to align 24-bit output to a 32-bit container */
		for (k = 0; k < 16; k++) {
			dst[k] = ch[k] << 8;
		}

		dst += 16;
	}
}

void capture_urb_complete(struct urb *urb)
{
	struct us1800_card *us1800 = urb->context;
	struct snd_pcm_substream *substream;
	struct snd_pcm_runtime *runtime;
	unsigned long flags;
	int frames_received;
	snd_pcm_uframes_t write_pos;
	snd_pcm_uframes_t buffer_size, period_size;
	bool need_period_elapsed = false;

	if (!us1800)
		return;

	if (!us1800->dev) {
		usb_unanchor_urb(urb);
		atomic_dec(&us1800->active_urbs);
		return;
	}

	if (urb->status) {
		usb_unanchor_urb(urb);
		atomic_dec(&us1800->active_urbs);
		return;
	}

	substream = us1800->capture_substream;
	if (!substream || !substream->runtime) {
		usb_unanchor_urb(urb);
		atomic_dec(&us1800->active_urbs);
		return;
	}
	runtime = substream->runtime;
	if (!runtime->dma_area) {
		usb_unanchor_urb(urb);
		atomic_dec(&us1800->active_urbs);
		return;
	}

	buffer_size = runtime->buffer_size;
	period_size = runtime->period_size;

	if (urb->actual_length % 64 != 0)
		dev_warn_ratelimited(&us1800->dev->dev, "Unaligned capture packet size: %d\n", urb->actual_length);
	frames_received = urb->actual_length / 64;

	if (frames_received > 0) {
		spin_lock_irqsave(&us1800->lock, flags);

		if (!atomic_read(&us1800->capture_active)) {
			spin_unlock_irqrestore(&us1800->lock, flags);
			atomic_dec(&us1800->active_urbs);
			return;
		}

		write_pos = us1800->driver_capture_pos;
		u32 *dma_ptr = (u32 *)(runtime->dma_area + frames_to_bytes(runtime, write_pos));

		if (write_pos + frames_received <= buffer_size) {
			us1800_decode_capture_chunk(urb->transfer_buffer, dma_ptr, frames_received);
		} else {
			int part1 = buffer_size - write_pos;
			int part2 = frames_received - part1;

			us1800_decode_capture_chunk(urb->transfer_buffer, dma_ptr, part1);
			us1800_decode_capture_chunk(urb->transfer_buffer + (part1 * 64), (u32 *)runtime->dma_area, part2);
		}

		us1800->driver_capture_pos += frames_received;
		if (us1800->driver_capture_pos >= buffer_size)
			us1800->driver_capture_pos -= buffer_size;

		us1800->capture_frames_processed += frames_received;

		if (period_size > 0) {
			u64 current_period = div_u64(us1800->capture_frames_processed, period_size);

			if (current_period > us1800->last_cap_period_pos) {
				us1800->last_cap_period_pos = current_period;
				need_period_elapsed = true;
			}
		}
		spin_unlock_irqrestore(&us1800->lock, flags);

		if (need_period_elapsed)
			snd_pcm_period_elapsed(substream);
	}

	usb_anchor_urb(urb, &us1800->capture_anchor);
	if (usb_submit_urb(urb, GFP_ATOMIC) < 0) {
		usb_unanchor_urb(urb);
		atomic_dec(&us1800->active_urbs);
		return;
	}
}

const struct snd_pcm_ops us1800_capture_ops = {
	.open = us1800_capture_open,
	.close = us1800_capture_close,
	.ioctl = snd_pcm_lib_ioctl,
	.hw_params = us1800_pcm_hw_params,
	.hw_free = NULL,
	.prepare = us1800_capture_prepare,
	.trigger = us1800_capture_trigger,
	.pointer = us1800_capture_pointer,
};
