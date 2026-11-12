#include "AdaOnboarding.h"
#include <string.h>

void AdaOnboardingInit(AdaOnboarding *s) {
    memset(s, 0, sizeof(*s));
    s->version = 1;
    s->russian = 1;
    s->guidance = 1;
}

int AdaOnboardingValid(const AdaOnboarding *s) {
    if (!s || s->version != 1 || s->step < AdaWelcome || s->step >= AdaStepCount ||
        s->russian > 1 || s->guidance > 1 || s->microphone_replayed > 1 || s->speaker_played > 1) return 0;
    AdaTestResult results[] = {s->speaker, s->display, s->touch[0], s->touch[1],
                             s->touch[2], s->touch[3], s->microphone};
    for (unsigned i = 0; i < sizeof(results) / sizeof(results[0]); i++)
        if (results[i] < AdaUnknown || results[i] > AdaFailed) return 0;
    if (s->microphone == AdaPassed && !s->microphone_replayed) return 0;
    if (s->speaker == AdaPassed && !s->speaker_played) return 0;
    return 1;
}

int AdaOnboardingApply(AdaOnboarding *s, AdaOnboardingEvent event) {
    if (!AdaOnboardingValid(s)) return 0;
    if (event == AdaRepeat) return 1; /* Replay never certifies a test. */
    if (event == AdaPromptCompleted) {
        if (s->step != AdaSpeaker) return 0;
        s->speaker_played = 1; return 1;
    }
    if (s->step == AdaSpeaker && event == AdaConfirm && !s->speaker_played) return 0;
    if (s->step == AdaWelcome || s->step == AdaSummary) {
        if (event != AdaContinue) return 0;
        s->step = s->step == AdaWelcome ? AdaSpeaker : AdaConnection;
        return 1;
    }
    if (s->step == AdaConnection) return 0; /* Authentication is not a diagnostic. */
    if (s->step == AdaMicrophone) {
        if (event == AdaReplaySucceeded || event == AdaReplayFailed || event == AdaReplayStarted) {
            s->microphone_replayed = event == AdaReplaySucceeded;
            s->microphone = event == AdaReplayFailed ? AdaFailed : AdaUnknown;
            return 1;
        }
        if (event == AdaConfirm && !s->microphone_replayed) return 0;
    } else if (event == AdaReplaySucceeded || event == AdaReplayFailed || event == AdaReplayStarted || event == AdaPermissionDenied) {
        return 0;
    }
    AdaTestResult result;
    if (event == AdaConfirm) result = AdaPassed;
    else if (event == AdaMarkUnclear) result = AdaUnclear;
    else if (event == AdaSkip) result = AdaSkipped;
    else if (event == AdaPermissionDenied) result = AdaDenied;
    else return 0;
    if (s->step == AdaSpeaker) s->speaker = result;
    else if (s->step == AdaDisplay) s->display = result;
    else if (s->step >= AdaTouchTop && s->step <= AdaTouchRight) s->touch[s->step - AdaTouchTop] = result;
    else s->microphone = result;
    s->step = (AdaOnboardingStep)(s->step + 1);
    return 1;
}

const char *AdaOnboardingStepID(AdaOnboardingStep step) {
    static const char *ids[] = {"welcome", "speaker", "display", "touch_top", "touch_bottom",
        "touch_left", "touch_right", "microphone", "summary", "connection"};
    return step >= AdaWelcome && step < AdaStepCount ? ids[step] : "invalid";
}
