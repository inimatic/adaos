#include "AdaVoiceDetector.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

void AdaVoiceDetectorReset(AdaVoiceDetector *d, int recalibrate) {
    d->carry = d->pre_count = d->count = 0;
    d->voiced = d->quiet = d->dropping = 0;
    d->level = 0;
    if (recalibrate) { d->calibration_count = 0; d->noise = 40; }
}
static int Compare(const void *a, const void *b) {
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}
static void Frame(AdaVoiceDetector *d, AdaVoiceSegment emit, void *context) {
    double sum = 0, square = 0;
    for (size_t i = 0; i < ADA_VOICE_FRAME; i++) {
        double x = d->frame[i]; sum += x; square += x*x;
    }
    double mean = sum / ADA_VOICE_FRAME;
    d->level = sqrt(fmax(0, square / ADA_VOICE_FRAME - mean*mean));
    if (d->calibration_count < 30) {
        d->calibration[d->calibration_count++] = d->level;
        if (d->calibration_count == 30) {
            qsort(d->calibration, 30, sizeof(double), Compare);
            d->noise = fmax(40, d->calibration[6]);
        }
        return;
    }
    int active = d->count || d->dropping;
    int voice = d->level >= fmax(active ? 220 : 300, d->noise * (active ? 2 : 3));
    if (voice) d->quiet = 0;
    else {
        d->quiet++;
        if (!d->count) d->noise = fmax(40, d->noise*.98 + d->level*.02);
    }
    if (d->dropping) {
        d->noise = fmax(40, d->noise*.98 + d->level*.02);
        if (d->quiet >= 40) d->dropping = 0;
        return;
    }
    if (d->count) {
        if (d->count + ADA_VOICE_FRAME >= ADA_VOICE_MAX) {
            d->count = d->pre_count = d->voiced = 0;
            d->dropping = 1; d->too_long++; return; /* Never dispatch a truncated tail. */
        }
        AdaVoiceCopy(d->speech + d->count, d->frame, sizeof(d->frame));
        d->count += ADA_VOICE_FRAME;
        if (voice) d->voiced++;
        if (d->quiet >= 40) {
            size_t count = d->count;
            int complete = d->voiced >= 8;
            d->count = d->pre_count = d->voiced = 0;
            if (complete) { d->segments++; if (emit) emit(context, d->speech, count); }
        }
        return;
    }
    if (d->pre_count + ADA_VOICE_FRAME > ADA_VOICE_PREROLL) {
        AdaVoiceCopy(d->pre, d->pre + ADA_VOICE_FRAME, (d->pre_count - ADA_VOICE_FRAME)*sizeof(int16_t));
        d->pre_count -= ADA_VOICE_FRAME;
    }
    AdaVoiceCopy(d->pre + d->pre_count, d->frame, sizeof(d->frame));
    d->pre_count += ADA_VOICE_FRAME;
    if (voice) {
        AdaVoiceCopy(d->speech, d->pre, d->pre_count*sizeof(int16_t));
        d->count = d->pre_count; d->voiced = 1;
    }
}
void AdaVoiceDetectorPush(AdaVoiceDetector *d, const int16_t *pcm, size_t count,
                          AdaVoiceSegment emit, void *context) {
    while (count) {
        size_t take = ADA_VOICE_FRAME - d->carry;
        if (take > count) take = count;
        AdaVoiceCopy(d->frame + d->carry, pcm, take*sizeof(int16_t));
        d->carry += take; pcm += take; count -= take;
        if (d->carry == ADA_VOICE_FRAME) { d->carry = 0; Frame(d, emit, context); }
    }
}
