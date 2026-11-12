#include "AdaVoiceGate.h"
#include <assert.h>
#include <math.h>
int main(void) {
    AdaVoiceGate g = {0};
    assert(!AdaVoiceGateWaiting(&g, 1) && !AdaVoiceGateTake(&g));
    AdaVoiceGateArm(&g, 100);
    assert(AdaVoiceGateWaiting(&g, 105.999));
    assert(!AdaVoiceGateWaiting(&g, 106));
    AdaVoiceGateBegin(&g, 106); assert(!AdaVoiceGateTake(&g));
    AdaVoiceGateArm(&g, 200); AdaVoiceGateBegin(&g, 205.9);
    assert(!AdaVoiceGateWaiting(&g, 210));
    assert(AdaVoiceGateTake(&g)); /* Late STT does not reject timely speech. */
    assert(!AdaVoiceGateTake(&g)); /* Never admit a second unrelated phrase. */
    AdaVoiceGateArm(&g, 300); AdaVoiceGateBegin(&g, 301);
    AdaVoiceGateClear(&g); assert(!AdaVoiceGateTake(&g)); /* Stop/offline/TTS. */
    AdaVoiceGateArm(&g, 400); AdaVoiceGateClear(&g);
    AdaVoiceGateBegin(&g, 401); assert(!AdaVoiceGateTake(&g));
    AdaVoiceGateArm(&g, NAN); assert(!AdaVoiceGateWaiting(&g, 1));
    AdaVoiceGateArm(&g, 500); assert(!AdaVoiceGateWaiting(&g, NAN));
    return 0;
}
