// SPDX-License-Identifier: GPL-2.0-only
// Copyright (c) 2026 Šerif Rami <ramiserifpersia@gmail.com>

#include "us1800_pcm.h"

const struct snd_pcm_hardware us1800_playback_hw = {
	.info = (SNDRV_PCM_INFO_MMAP | SNDRV_PCM_INFO_INTERLEAVED |
	SNDRV_PCM_INFO_BLOCK_TRANSFER | SNDRV_PCM_INFO_MMAP_VALID |
	SNDRV_PCM_INFO_PAUSE | SNDRV_PCM_INFO_RESUME),
	.formats = SNDRV_PCM_FMTBIT_S24_3LE,
	.rates = (SNDRV_PCM_RATE_44100 | SNDRV_PCM_RATE_48000 |
	SNDRV_PCM_RATE_88200 | SNDRV_PCM_RATE_96000),
	.rate_min = 44100,
	.rate_max = 96000,
	.channels_min = PLAYBACK_CHANNELS,
	.channels_max = PLAYBACK_CHANNELS,
	.buffer_bytes_max = 1024 * 1024,
	.period_bytes_min = 576,
	.period_bytes_max = 1024 * 1024,
	.periods_min = 2,
	.periods_max = 1024,
};

static int us1800_playback_open(struct snd_pcm_substream *substream)
{
	struct us1800_card *us1800 = snd_pcm_substream_chip(substream);

	substream->runtime->hw = us1800_playback_hw;
	us1800->playback_substream = substream;
	atomic_set(&us1800->playback_active, 0);
	return 0;
}

static int us1800_playback_close(struct snd_pcm_substream *substream)
{
	struct us1800_card *us1800 = snd_pcm_substream_chip(substream);

	atomic_set(&us1800->playback_active, 0);

	usb_kill_anchored_urbs(&us1800->playback_anchor);
	usb_kill_anchored_urbs(&us1800->feedback_anchor);

	us1800->playback_substream = NULL;
	return 0;
}

static int us1800_playback_prepare(struct snd_pcm_substream *substream)
{
	struct us1800_card *us1800 = snd_pcm_substream_chip(substream);
	struct snd_pcm_runtime *runtime = substream->runtime;
	int i, u;
	size_t nominal_bytes = (runtime->rate / 8000) * PLAYBACK_FRAME_SIZE;

	usb_kill_anchored_urbs(&us1800->playback_anchor);
	usb_kill_anchored_urbs(&us1800->feedback_anchor);

	us1800->driver_playback_pos = 0;
	us1800->playback_frames_consumed = 0;
	us1800->last_pb_period_pos = 0;
	us1800->feedback_synced = false;

	us1800->feedback_urb_skip_count = 4;

	us1800->phase_accum = 0;
	us1800->freq_q16 = div_u64(((u64)runtime->rate << 16), 8000);

	for (i = 0; i < NUM_FEEDBACK_URBS; i++) {
		struct urb *f_urb = us1800->feedback_urbs[i];

		f_urb->number_of_packets = FEEDBACK_URB_PACKETS;
		f_urb->transfer_buffer_length = FEEDBACK_URB_PACKETS * FEEDBACK_PACKET_SIZE;
		for (u = 0; u < FEEDBACK_URB_PACKETS; u++) {
			f_urb->iso_frame_desc[u].offset = u * FEEDBACK_PACKET_SIZE;
			f_urb->iso_frame_desc[u].length = FEEDBACK_PACKET_SIZE;
		}
	}

	for (u = 0; u < NUM_PLAYBACK_URBS; u++) {
		struct urb *urb = us1800->playback_urbs[u];
		size_t total_bytes = 0;

		urb->number_of_packets = PLAYBACK_URB_PACKETS;

		for (i = 0; i < PLAYBACK_URB_PACKETS; i++) {
			urb->iso_frame_desc[i].offset = i * nominal_bytes;
			urb->iso_frame_desc[i].length = nominal_bytes;
			total_bytes += nominal_bytes;
		}

		urb->transfer_buffer_length = total_bytes;
		memset(urb->transfer_buffer, 0, total_bytes);
	}
	return 0;
}

static snd_pcm_uframes_t us1800_playback_pointer(struct snd_pcm_substream *substream)
{
	struct us1800_card *us1800 = snd_pcm_substream_chip(substream);
	unsigned long flags;
	u64 pos;

	if (!atomic_read(&us1800->playback_active))
		return 0;

	spin_lock_irqsave(&us1800->lock, flags);
	pos = us1800->playback_frames_consumed;
	spin_unlock_irqrestore(&us1800->lock, flags);

	return (snd_pcm_uframes_t)(pos % substream->runtime->buffer_size);
}

static int us1800_playback_trigger(struct snd_pcm_substream *substream, int cmd)
{
	struct us1800_card *us1800 = snd_pcm_substream_chip(substream);
	int i, ret = 0;
	unsigned long flags;

	switch (cmd) {
		case SNDRV_PCM_TRIGGER_START:
		case SNDRV_PCM_TRIGGER_RESUME:
		case SNDRV_PCM_TRIGGER_PAUSE_RELEASE:
			spin_lock_irqsave(&us1800->lock, flags);
			if (us1800->rate_changing) {
				spin_unlock_irqrestore(&us1800->lock, flags);
				return -EBUSY;
			}
			if (atomic_read(&us1800->playback_active)) {
				spin_unlock_irqrestore(&us1800->lock, flags);
				return 0;
			}
			atomic_set(&us1800->playback_active, 1);
			us1800->feedback_synced = false;
			spin_unlock_irqrestore(&us1800->lock, flags);

			for (i = 0; i < NUM_FEEDBACK_URBS; i++) {
				usb_anchor_urb(us1800->feedback_urbs[i], &us1800->feedback_anchor);
				if (usb_submit_urb(us1800->feedback_urbs[i], GFP_ATOMIC) < 0) {
					usb_unanchor_urb(us1800->feedback_urbs[i]);
					ret = -EIO;
					goto error;
				}
				atomic_inc(&us1800->active_urbs);
			}

			for (i = 0; i < NUM_PLAYBACK_URBS; i++) {
				usb_anchor_urb(us1800->playback_urbs[i], &us1800->playback_anchor);
				if (usb_submit_urb(us1800->playback_urbs[i], GFP_ATOMIC) < 0) {
					usb_unanchor_urb(us1800->playback_urbs[i]);
					ret = -EIO;
					goto error;
				}
				atomic_inc(&us1800->active_urbs);
			}
			break;

		case SNDRV_PCM_TRIGGER_STOP:
		case SNDRV_PCM_TRIGGER_SUSPEND:
		case SNDRV_PCM_TRIGGER_PAUSE_PUSH:
			atomic_set(&us1800->playback_active, 0);

			for (i = 0; i < NUM_PLAYBACK_URBS; i++) {
				if (us1800->playback_urbs[i])
					usb_unlink_urb(us1800->playback_urbs[i]);
			}
			for (i = 0; i < NUM_FEEDBACK_URBS; i++) {
				if (us1800->feedback_urbs[i])
					usb_unlink_urb(us1800->feedback_urbs[i]);
			}

			if (!atomic_read(&us1800->capture_active)) {
				schedule_work(&us1800->stop_work);
			}
			break;

		default:
			return -EINVAL;
	}

	return 0;

	error:
	atomic_set(&us1800->playback_active, 0);
	usb_kill_anchored_urbs(&us1800->playback_anchor);
	usb_kill_anchored_urbs(&us1800->feedback_anchor);
	return ret;
}

void playback_urb_complete(struct urb *urb)
{
	struct us1800_card *us1800 = urb->context;
	struct snd_pcm_substream *substream;
	struct snd_pcm_runtime *runtime;
	size_t total_bytes_for_urb = 0;
	snd_pcm_uframes_t frames_to_copy;
	int i;
	unsigned long flags;
	bool need_period_elapsed = false;

	if (!us1800)
		return;

	if (urb->status == -ENOENT || urb->status == -ECONNRESET ||
		urb->status == -ESHUTDOWN)
		goto exit_clear;

	if (!atomic_read(&us1800->playback_active))
		goto exit_clear;

	substream = us1800->playback_substream;
	runtime = substream->runtime;

	spin_lock_irqsave(&us1800->lock, flags);

	for (i = 0; i < urb->number_of_packets; i++) {
		unsigned int frames_for_packet;

		us1800->phase_accum += us1800->freq_q16;
		frames_for_packet = us1800->phase_accum >> 16;
		us1800->phase_accum &= 0xFFFF;

		if (frames_for_packet > MAX_FRAMES_PER_PACKET)
			frames_for_packet = MAX_FRAMES_PER_PACKET;

		urb->iso_frame_desc[i].offset = total_bytes_for_urb;
		urb->iso_frame_desc[i].length = frames_for_packet * PLAYBACK_FRAME_SIZE;
		total_bytes_for_urb += urb->iso_frame_desc[i].length;
	}
	urb->transfer_buffer_length = total_bytes_for_urb;

	if (total_bytes_for_urb > 0) {
		u8 *dst_buf = urb->transfer_buffer;
		size_t ptr_bytes = frames_to_bytes(runtime, us1800->driver_playback_pos);
		frames_to_copy = bytes_to_frames(runtime, total_bytes_for_urb);

		if (us1800->driver_playback_pos + frames_to_copy > runtime->buffer_size) {
			size_t part1 = runtime->buffer_size - us1800->driver_playback_pos;
			size_t part1_bytes = frames_to_bytes(runtime, part1);

			memcpy(dst_buf, runtime->dma_area + ptr_bytes, part1_bytes);
			memcpy(dst_buf + part1_bytes, runtime->dma_area, total_bytes_for_urb - part1_bytes);
		} else {
			memcpy(dst_buf, runtime->dma_area + ptr_bytes, total_bytes_for_urb);
		}

		us1800->driver_playback_pos += frames_to_copy;
		if (us1800->driver_playback_pos >= runtime->buffer_size)
			us1800->driver_playback_pos -= runtime->buffer_size;

		us1800->playback_frames_consumed += frames_to_copy;

		if (div_u64(us1800->playback_frames_consumed, runtime->period_size) > us1800->last_pb_period_pos) {
			us1800->last_pb_period_pos = div_u64(us1800->playback_frames_consumed, runtime->period_size);
			need_period_elapsed = true;
		}
	}
	spin_unlock_irqrestore(&us1800->lock, flags);

	if (need_period_elapsed)
		snd_pcm_period_elapsed(substream);

	usb_anchor_urb(urb, &us1800->playback_anchor);
	if (usb_submit_urb(urb, GFP_ATOMIC) < 0)
		goto exit_clear;

	return;

	exit_clear:
	usb_unanchor_urb(urb);
	atomic_dec(&us1800->active_urbs);
}

void feedback_urb_complete(struct urb *urb)
{
	struct us1800_card *us1800 = urb->context;
	unsigned long flags;
	int p;

	if (!us1800)
		return;

	if (urb->status || !atomic_read(&us1800->playback_active)) {
		usb_unanchor_urb(urb);
		atomic_dec(&us1800->active_urbs);
		return;
	}

	spin_lock_irqsave(&us1800->lock, flags);

	if (us1800->feedback_urb_skip_count > 0) {
		us1800->feedback_urb_skip_count--;
		spin_unlock_irqrestore(&us1800->lock, flags);
		goto resubmit;
	}

	for (p = 0; p < urb->number_of_packets; p++) {
		if (urb->iso_frame_desc[p].status == 0 && urb->iso_frame_desc[p].actual_length >= 1) {
			u8 *data = (u8 *)urb->transfer_buffer + urb->iso_frame_desc[p].offset;
			u32 sum_frames_3ms;
			u32 target_freq_q16;

			if (urb->iso_frame_desc[p].actual_length >= 3) {
				sum_frames_3ms = data[0] + data[1] + data[2];
			} else {
				sum_frames_3ms = data[0] * 3;
			}

			target_freq_q16 = (sum_frames_3ms << 16) / 24;

			us1800->freq_q16 = (us1800->freq_q16 * PLL_FILTER_OLD_WEIGHT +
			target_freq_q16 * PLL_FILTER_NEW_WEIGHT) / PLL_FILTER_DIVISOR;

			us1800->feedback_synced = true;
		}
	}
	spin_unlock_irqrestore(&us1800->lock, flags);

	resubmit:
	usb_anchor_urb(urb, &us1800->feedback_anchor);
	if (usb_submit_urb(urb, GFP_ATOMIC) < 0) {
		usb_unanchor_urb(urb);
		atomic_dec(&us1800->active_urbs);
	}
}

const struct snd_pcm_ops us1800_playback_ops = {
	.open = us1800_playback_open,
	.close = us1800_playback_close,
	.ioctl = snd_pcm_lib_ioctl,
	.hw_params = us1800_pcm_hw_params,
	.hw_free = NULL,
	.prepare = us1800_playback_prepare,
	.trigger = us1800_playback_trigger,
	.pointer = us1800_playback_pointer,
};
