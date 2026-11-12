#ifndef ADA_VOICE_METRICS_H
#define ADA_VOICE_METRICS_H
#include <stddef.h>
#include <stdint.h>
typedef struct { double rms; unsigned tone_hz, tone_ppm, clipped; } AdaVoiceMetrics;
/* Diagnostic only: PCM16 mono/16 kHz. No speech or activation decisions. */
AdaVoiceMetrics AdaVoiceMeasure(const int16_t *pcm, size_t count);
#endif
