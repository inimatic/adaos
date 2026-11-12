#include "AdaVoiceGate.h"
#include <math.h>
void AdaVoiceGateClear(AdaVoiceGate *g) { g->deadline = 0; g->begun = 0; }
void AdaVoiceGateArm(AdaVoiceGate *g, double now) {
    AdaVoiceGateClear(g);
    if (isfinite(now) && now >= 0) g->deadline = now + 6;
}
int AdaVoiceGateWaiting(const AdaVoiceGate *g, double now) {
    return isfinite(now) && g->deadline > 0 && now < g->deadline;
}
void AdaVoiceGateBegin(AdaVoiceGate *g, double now) {
    if (AdaVoiceGateWaiting(g, now)) g->begun = 1;
}
int AdaVoiceGateTake(AdaVoiceGate *g) {
    int accepted = g->begun != 0;
    AdaVoiceGateClear(g); /* One utterance, including failed/empty recognition. */
    return accepted;
}
