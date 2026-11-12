#include "AdaVoiceDetector.h"
#include "AdaVoiceMetrics.h"
#include <assert.h>
#include <stdlib.h>

static unsigned emitted;
static size_t length;
static void Receive(void *ctx, const int16_t *pcm, size_t count) {
    (void)ctx; (void)pcm; emitted++; length = count;
}
static void Feed(AdaVoiceDetector *d, unsigned frames, int amplitude, int dc) {
    int16_t pcm[320];
    for (unsigned i=0; i<320; i++) pcm[i] = (int16_t)(dc + (i%2 ? amplitude : -amplitude));
    for (unsigned i=0; i<frames; i++) {
        /* Exercise chunk boundaries that do not align with a detector frame. */
        AdaVoiceDetectorPush(d, pcm, 73, Receive, NULL);
        AdaVoiceDetectorPush(d, pcm+73, 247, Receive, NULL);
    }
}
int main(void) {
    int16_t tone[16000];
    for (unsigned i=0; i<16000; i++) tone[i]=(int16_t)(2000+(i%16<8 ? 1000 : -1000));
    AdaVoiceMetrics m=AdaVoiceMeasure(tone,16000);
    assert(m.rms>999 && m.rms<1001 && m.tone_hz==1000 && m.tone_ppm>800000 && !m.clipped);
    for (unsigned i=0; i<16000; i++) tone[i]=2000;
    m=AdaVoiceMeasure(tone,16000); assert(m.rms==0 && m.tone_ppm==0);
    tone[0]=32767; tone[1]=-32768; m=AdaVoiceMeasure(tone,16000); assert(m.clipped==2);
    AdaVoiceDetector *d = calloc(1, sizeof(*d)); assert(d);
    AdaVoiceDetectorReset(d, 1);
    Feed(d, 100, 100, 2000); assert(!emitted && d->calibration_count == 30);
    Feed(d, 80, 1800, 2000); Feed(d, 60, 100, 2000);
    assert(emitted == 1 && length >= 32000 && length < ADA_VOICE_MAX);
    Feed(d, 10000, 100, 2000); assert(emitted == 1 && !d->count && d->pre_count <= ADA_VOICE_PREROLL);
    Feed(d, 700, 2000, 0); Feed(d, 80, 100, 0);
    assert(emitted == 1 && d->too_long == 1); /* No overlong phrase or trailing command. */
    AdaVoiceDetectorReset(d, 1); Feed(d, 40, 100, 0);
    Feed(d, 40, 2000, 0); AdaVoiceDetectorReset(d, 0); Feed(d, 60, 100, 0);
    assert(emitted == 1); /* Stop/TTS clears partial audio. */
    Feed(d, 5, 2000, 0); Feed(d, 60, 100, 0); assert(emitted == 1);
    Feed(d, 60, 2000, 0); Feed(d, 60, 100, 0); assert(emitted == 2);
    free(d); return 0;
}
