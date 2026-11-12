#include "../../jailbreak/smoke/AdaOnboarding.h"
#include <assert.h>
#include <stdio.h>

int main(void) {
    AdaOnboarding s;
    AdaOnboardingInit(&s);
    assert(AdaOnboardingValid(&s));
    assert(!AdaOnboardingApply(&s, AdaConfirm));
    assert(AdaOnboardingApply(&s, AdaContinue) && s.step == AdaSpeaker);
    for (int i = 0; i < 3; i++) assert(AdaOnboardingApply(&s, AdaRepeat));
    assert(s.step == AdaSpeaker && s.speaker == AdaUnknown);
    assert(!AdaOnboardingApply(&s, AdaConfirm));
    assert(AdaOnboardingApply(&s, AdaPromptCompleted) && s.speaker == AdaUnknown);
    assert(!AdaOnboardingApply(&s, AdaReplaySucceeded));
    assert(AdaOnboardingApply(&s, AdaMarkUnclear) && s.speaker == AdaUnclear);
    assert(AdaOnboardingApply(&s, AdaConfirm) && s.display == AdaPassed);
    assert(AdaOnboardingApply(&s, AdaConfirm) && s.touch[0] == AdaPassed);
    assert(AdaOnboardingApply(&s, AdaSkip) && s.touch[1] == AdaSkipped);
    assert(AdaOnboardingApply(&s, AdaMarkUnclear) && s.touch[2] == AdaUnclear);
    assert(AdaOnboardingApply(&s, AdaConfirm) && s.touch[3] == AdaPassed);
    assert(s.step == AdaMicrophone);
    assert(!AdaOnboardingApply(&s, AdaConfirm));
    assert(AdaOnboardingApply(&s, AdaReplayFailed));
    assert(!AdaOnboardingApply(&s, AdaConfirm));
    assert(AdaOnboardingApply(&s, AdaReplaySucceeded));
    assert(s.microphone == AdaUnknown); /* Playback alone is not human hearing. */
    assert(AdaOnboardingApply(&s, AdaReplayStarted) && !s.microphone_replayed);
    assert(!AdaOnboardingApply(&s, AdaConfirm));
    assert(AdaOnboardingApply(&s, AdaReplaySucceeded));
    assert(AdaOnboardingApply(&s, AdaConfirm) && s.step == AdaSummary);
    AdaOnboarding restored = s;
    assert(AdaOnboardingValid(&restored) && restored.microphone == AdaPassed);
    assert(AdaOnboardingApply(&restored, AdaContinue) && restored.step == AdaConnection);
    assert(!AdaOnboardingApply(&restored, AdaContinue)); /* Never fake admission. */
    restored.microphone_replayed = 0;
    assert(!AdaOnboardingValid(&restored));
    restored = s; restored.step = AdaStepCount;
    assert(!AdaOnboardingValid(&restored));
    restored = s; restored.version = 99;
    assert(!AdaOnboardingValid(&restored));
    restored = s; restored.touch[0] = (AdaTestResult)-1;
    assert(!AdaOnboardingValid(&restored));
    AdaOnboardingInit(&s);
    AdaOnboardingApply(&s, AdaContinue);
    while (s.step < AdaMicrophone) assert(AdaOnboardingApply(&s, AdaSkip));
    assert(AdaOnboardingApply(&s, AdaPermissionDenied) && s.step == AdaSummary);
    assert(s.microphone == AdaDenied && s.speaker == AdaSkipped);
    puts("onboarding transitions: pass");
    return 0;
}
