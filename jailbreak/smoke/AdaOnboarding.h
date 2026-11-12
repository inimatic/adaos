#ifndef ADA_ONBOARDING_H
#define ADA_ONBOARDING_H

/* Platform-neutral diagnostic transitions. Permissions and playback are adapters. */
typedef enum {
    AdaWelcome, AdaSpeaker, AdaDisplay, AdaTouchTop, AdaTouchBottom,
    AdaTouchLeft, AdaTouchRight, AdaMicrophone, AdaSummary, AdaConnection,
    AdaStepCount
} AdaOnboardingStep;

typedef enum { AdaUnknown, AdaPassed, AdaUnclear, AdaSkipped, AdaDenied, AdaFailed } AdaTestResult;
typedef enum {
    AdaContinue, AdaConfirm, AdaMarkUnclear, AdaSkip, AdaPermissionDenied,
    AdaReplaySucceeded, AdaReplayFailed, AdaRepeat, AdaReplayStarted, AdaPromptCompleted
} AdaOnboardingEvent;

typedef struct {
    unsigned version;
    AdaOnboardingStep step;
    AdaTestResult speaker, display, touch[4], microphone;
    unsigned russian, guidance, microphone_replayed, speaker_played;
} AdaOnboarding;

void AdaOnboardingInit(AdaOnboarding *state);
int AdaOnboardingValid(const AdaOnboarding *state);
int AdaOnboardingApply(AdaOnboarding *state, AdaOnboardingEvent event);
const char *AdaOnboardingStepID(AdaOnboardingStep step);

#endif
