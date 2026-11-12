#ifndef ADA_VOICE_DETECTOR_H
#define ADA_VOICE_DETECTOR_H
#include <stddef.h>
#include <stdint.h>
#if defined(__APPLE__)
/* The pinned 10.3 SDK stubs omit memcpy/memmove's resolver exports. bcopy is
 * the public overlap-safe libc equivalent. A distinct C name avoids Clang
 * lowering the SDK's inline bcopy back into the missing memmove stub. */
extern void AdaVoiceDarwinCopy(const void *, void *, size_t) __asm__("_bcopy");
#define AdaVoiceCopy(dst, src, size) AdaVoiceDarwinCopy((src), (dst), (size))
#else
#include <string.h>
#define AdaVoiceCopy(dst, src, size) memmove((dst), (src), (size))
#endif

/* PCM16 mono/16 kHz. Mirrors the Android/Desktop detector's DC-free energy,
 * calibration and hysteresis. No inference, network, or persistent audio. */
#define ADA_VOICE_FRAME 320
#define ADA_VOICE_PREROLL 8000
#define ADA_VOICE_MAX 192000 /* 12 seconds, including preroll/trailing silence */
typedef struct {
    int16_t frame[ADA_VOICE_FRAME], pre[ADA_VOICE_PREROLL], speech[ADA_VOICE_MAX];
    size_t carry, pre_count, count;
    unsigned calibration_count, voiced, quiet, dropping;
    double calibration[30], noise, level;
    unsigned segments, too_long;
} AdaVoiceDetector;
typedef void (*AdaVoiceSegment)(void *, const int16_t *, size_t);
void AdaVoiceDetectorReset(AdaVoiceDetector *d, int recalibrate);
void AdaVoiceDetectorPush(AdaVoiceDetector *d, const int16_t *pcm, size_t count,
                          AdaVoiceSegment emit, void *context);
#endif
