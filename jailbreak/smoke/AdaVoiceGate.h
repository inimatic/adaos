#ifndef ADA_VOICE_GATE_H
#define ADA_VOICE_GATE_H

/* One following utterance, starting within six monotonic seconds of the cue.
 * Recognition may finish later. Stop, network loss and output clear the gate. */
typedef struct { double deadline; unsigned begun; } AdaVoiceGate;
void AdaVoiceGateClear(AdaVoiceGate *gate);
void AdaVoiceGateArm(AdaVoiceGate *gate, double now);
int AdaVoiceGateWaiting(const AdaVoiceGate *gate, double now);
void AdaVoiceGateBegin(AdaVoiceGate *gate, double now);
int AdaVoiceGateTake(AdaVoiceGate *gate);

#endif
