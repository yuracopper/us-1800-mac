/* SPDX-License-Identifier: GPL-2.0-only */
// Copyright (c) 2026 Šerif Rami <ramiserifpersia@gmail.com>

#ifndef __US1800_PCM_H
#define __US1800_PCM_H

#include "us1800.h"

extern const struct snd_pcm_hardware us1800_playback_hw;
extern const struct snd_pcm_hardware us1800_capture_hw;

extern const struct snd_pcm_ops us1800_playback_ops;
extern const struct snd_pcm_ops us1800_capture_ops;

void playback_urb_complete(struct urb *urb);
void feedback_urb_complete(struct urb *urb);
void capture_urb_complete(struct urb *urb);
int us1800_configure_device_for_rate(struct us1800_card *us1800, int rate);
void us1800_stop_pcm_work_handler(struct work_struct *work);
int us1800_pcm_hw_params(struct snd_pcm_substream *substream,
						 struct snd_pcm_hw_params *params);

#endif /* __US1800_PCM_H */
