#import "AdaVoiceSetupStore.h"
static NSString *const Key = @"adaos.ios.voice-setup.v1";
@interface AdaVoiceSetupStore ()
@property(nonatomic, strong) NSUserDefaults *defaults;
@property(nonatomic, strong) NSMutableDictionary *values;
@property(nonatomic) NSTimeInterval began;
@end
@implementation AdaVoiceSetupStore
- (instancetype)init { return [self initWithDefaults:[NSUserDefaults standardUserDefaults]]; }
- (instancetype)initWithDefaults:(NSUserDefaults *)defaults {
    self = [super init]; if (!self) return nil;
    self.defaults = defaults; self.values = [NSMutableDictionary dictionary];
    id saved = [defaults objectForKey:Key];
    if ([saved isKindOfClass:[NSDictionary class]]) {
        for (NSString *key in @[@"enabled", @"finished", @"microphone", @"node", @"address", @"reply", @"synthesis", @"heard", @"hearing_checked", @"local_replay_completed"])
            if ([saved[key] isKindOfClass:[NSNumber class]]) self.values[key] = @([saved[key] boolValue]);
    }
    return self;
}
- (NSDictionary *)report {
    NSMutableDictionary *report = [self.values mutableCopy];
    report[@"schema"] = @"adaos.ios.voice-setup.v1";
    report[@"ready"] = @([self.values[@"microphone"] boolValue] && [self.values[@"node"] boolValue] &&
        [self.values[@"address"] boolValue] && [self.values[@"reply"] boolValue] &&
        [self.values[@"synthesis"] boolValue] && [self.values[@"heard"] boolValue]);
    return report;
}
- (void)save { [self.defaults setObject:self.report forKey:Key]; [self.defaults synchronize]; }
- (BOOL)enabled { return [self.values[@"enabled"] boolValue]; }
- (void)setEnabled:(BOOL)enabled { self.values[@"enabled"] = @(enabled); [self save]; }
- (void)begin {
    BOOL enabled = self.enabled, local = [self.values[@"local_replay_completed"] boolValue];
    [self.values removeAllObjects]; self.values[@"enabled"] = @(enabled);
    if (local) { self.values[@"local_replay_completed"] = @YES; self.values[@"microphone"] = @YES; }
    self.began = [NSDate date].timeIntervalSince1970; [self save];
}
- (void)observe:(NSDictionary *)activation speech:(NSDictionary *)speech {
    if (!self.began || [activation[@"updated_at"] doubleValue] < self.began) return;
    NSDictionary *before = self.report;
    if ([activation[@"captured_bytes"] unsignedIntegerValue]) self.values[@"microphone"] = @YES;
    if ([activation[@"recognized"] unsignedIntegerValue]) self.values[@"node"] = @YES;
    /* Standalone address and completed cue are specifically tested here. */
    if ([activation[@"ready_cues"] unsignedIntegerValue]) self.values[@"address"] = @YES;
    if ([activation[@"own_replies"] unsignedIntegerValue] && [activation[@"followup_commands"] unsignedIntegerValue]) self.values[@"reply"] = @YES;
    if ([self.values[@"reply"] boolValue] && [speech[@"updated_at"] doubleValue] >= self.began &&
        [speech[@"state"] isEqual:@"completed"]) self.values[@"synthesis"] = @YES;
    if (![before isEqual:self.report]) [self save];
}
- (void)confirmHeard:(BOOL)heard {
    if (![self.values[@"synthesis"] boolValue]) return; /* Human result is never inferred. */
    self.values[@"heard"] = @(heard); self.values[@"hearing_checked"] = @YES; [self save];
}
- (void)localReplayCompleted:(BOOL)completed {
    self.values[@"local_replay_completed"] = @(completed);
    if (completed) self.values[@"microphone"] = @YES;
    [self save];
}
- (void)finish { self.began = 0; self.values[@"finished"] = @YES; [self save]; }
@end
