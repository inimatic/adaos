#import "AdaOnboardingStore.h"
#import <UIKit/UIKit.h>
#import <math.h>

static NSString *const StateKey = @"adaos.onboarding.v1";
static BOOL IntegerInRange(id value, NSInteger limit) {
    if (![value isKindOfClass:[NSNumber class]]) return NO;
    double n = [value doubleValue];
    return isfinite(n) && n >= 0 && n <= limit && n == floor(n);
}

@implementation AdaOnboardingStore
- (instancetype)init {
    self = [super init];
    if (!self) return nil;
    AdaOnboardingInit(&_state);
    id stored = [[NSUserDefaults standardUserDefaults] objectForKey:StateKey];
    if (![stored isKindOfClass:[NSDictionary class]]) return self;
    NSArray *keys = @[@"version", @"step", @"russian", @"guidance", @"microphone_replayed", @"speaker_played",
                      @"speaker", @"display", @"top", @"bottom", @"left", @"right", @"microphone"];
    NSInteger limits[] = {1, AdaStepCount - 1, 1, 1, 1, 1, AdaFailed, AdaFailed,
                          AdaFailed, AdaFailed, AdaFailed, AdaFailed, AdaFailed};
    for (NSUInteger i = 0; i < keys.count; i++)
        if (!IntegerInRange(stored[keys[i]], limits[i])) return self;
    AdaOnboarding s = {[stored[@"version"] unsignedIntValue], [stored[@"step"] intValue],
        [stored[@"speaker"] intValue], [stored[@"display"] intValue],
        {[stored[@"top"] intValue], [stored[@"bottom"] intValue], [stored[@"left"] intValue], [stored[@"right"] intValue]},
        [stored[@"microphone"] intValue], [stored[@"russian"] unsignedIntValue],
        [stored[@"guidance"] unsignedIntValue], [stored[@"microphone_replayed"] unsignedIntValue],
        [stored[@"speaker_played"] unsignedIntValue]};
    if (AdaOnboardingValid(&s)) _state = s;
    return self;
}
- (NSDictionary *)checkpoint {
    AdaOnboarding s = self.state;
    return @{@"version": @(s.version), @"step": @(s.step), @"russian": @(s.russian),
        @"guidance": @(s.guidance), @"microphone_replayed": @(s.microphone_replayed), @"speaker_played": @(s.speaker_played),
        @"speaker": @(s.speaker), @"display": @(s.display), @"top": @(s.touch[0]),
        @"bottom": @(s.touch[1]), @"left": @(s.touch[2]), @"right": @(s.touch[3]), @"microphone": @(s.microphone)};
}
- (void)save {
    if (!AdaOnboardingValid(&_state)) return;
    NSUserDefaults *defaults = [NSUserDefaults standardUserDefaults];
    [defaults setObject:[self checkpoint] forKey:StateKey];
    [defaults synchronize];
}
- (BOOL)apply:(AdaOnboardingEvent)event {
    if (!AdaOnboardingApply(&_state, event)) return NO;
    [self save];
    return YES;
}
- (NSDictionary *)report {
    NSArray *results = @[@"unknown", @"passed", @"unclear", @"skipped", @"denied", @"failed"];
    AdaOnboarding s = self.state;
    if (!AdaOnboardingValid(&s)) return @{};
    NSMutableDictionary *tests = [NSMutableDictionary dictionary];
    tests[@"speaker"] = @{@"human_result": results[s.speaker], @"prompt_completed": @(s.speaker_played != 0)};
    tests[@"display"] = @{@"human_result": results[s.display]};
    NSMutableDictionary *zones = [NSMutableDictionary dictionary];
    NSArray *names = @[@"top", @"bottom", @"left", @"right"];
    for (NSUInteger i = 0; i < 4; i++) zones[names[i]] = @{@"result": results[s.touch[i]],
        @"input": s.touch[i] == AdaPassed ? @"screen_zone_tap" : @"operator_choice"};
    tests[@"touch"] = zones;
    tests[@"microphone"] = @{@"human_result": results[s.microphone], @"local_replay_completed": @(s.microphone_replayed != 0)};
    UIDevice *device = [UIDevice currentDevice];
    device.batteryMonitoringEnabled = YES;
    return @{@"schema": @"adaos.ios.onboarding-report.v1", @"step": @(s.step), @"tests": tests,
        @"platform": @{@"os": @"ios", @"os_version": device.systemVersion,
            @"form_factor": UI_USER_INTERFACE_IDIOM() == UIUserInterfaceIdiomPad ? @"tablet" : @"phone",
            @"app_version": [[NSBundle mainBundle] objectForInfoDictionaryKey:@"CFBundleShortVersionString"] ?: @"unknown"},
        @"power": @{@"charge_fraction": @(device.batteryLevel), @"state": @(device.batteryState)},
        @"permissions_granted_by_report": @NO};
}
@end
