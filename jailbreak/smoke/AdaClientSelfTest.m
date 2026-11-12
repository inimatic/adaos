#import "AdaClientSelfTest.h"
#import "AdaOnboardingStore.h"
#import "AdaSubnetSession.h"
#import "AdaSubnetClient.h"
#import "AdaClientUIProbe.h"
#import "AdaLocalUIProbe.h"

/* Deliberately in-memory: never manufacture diagnostic results in real preferences. */
@interface AdaMemoryDefaults : NSUserDefaults
@property(nonatomic, strong) NSMutableDictionary *values;
@end
@implementation AdaMemoryDefaults
- (instancetype)init { self = [super init]; if (self) self.values = [NSMutableDictionary dictionary]; return self; }
- (id)objectForKey:(NSString *)key { return self.values[key]; }
- (void)setObject:(id)value forKey:(NSString *)key { self.values[key] = value; }
- (BOOL)synchronize { return YES; }
@end

BOOL AdaClientContractSelfTest(void) {
    if (![AdaLocalUIProbe validJob:@{@"operation": @"keyboard-and-speech-check", @"expires_at": @1050} now:1000] ||
        [AdaLocalUIProbe validJob:@{@"operation": @"keyboard-and-speech-check", @"expires_at": @999} now:1000] ||
        [AdaLocalUIProbe validJob:@{@"operation": @"keyboard-and-speech-check", @"expires_at": @1201} now:1000] ||
        [AdaLocalUIProbe validJob:@{@"operation": @"record-audio", @"expires_at": @1050} now:1000]) return NO;
    if (![AdaClientUIProbe validJob:@{@"operation": @"conversational-text-check", @"expires_at": @1050} now:1000] ||
        [AdaClientUIProbe validJob:@{@"operation": @"conversational-text-check", @"expires_at": @999} now:1000] ||
        [AdaClientUIProbe validJob:@{@"operation": @"conversational-text-check", @"expires_at": @1200} now:1000] ||
        [AdaClientUIProbe validJob:@{@"operation": @"arbitrary-command", @"expires_at": @1050} now:1000]) return NO;
    AdaMemoryDefaults *defaults = [[AdaMemoryDefaults alloc] init];
    AdaOnboardingStore *store = [[AdaOnboardingStore alloc] initWithDefaults:defaults];
    if (![store apply:AdaContinue] || [store apply:AdaConfirm]) return NO;
    if (![store apply:AdaPromptCompleted] || ![store apply:AdaConfirm]) return NO;
    AdaOnboardingStore *restored = [[AdaOnboardingStore alloc] initWithDefaults:defaults];
    if (restored.state.step != AdaDisplay || restored.state.speaker != AdaPassed) return NO;
    if (![restored apply:AdaSkip] || ![restored apply:AdaConfirm]) return NO;
    restored = [[AdaOnboardingStore alloc] initWithDefaults:defaults];
    if (restored.state.step != AdaTouchBottom || restored.state.touch[0] != AdaPassed) return NO;
    NSMutableDictionary *bad = [defaults.values[@"adaos.onboarding.v1"] mutableCopy];
    bad[@"step"] = @999;
    defaults.values[@"adaos.onboarding.v1"] = bad;
    if ([[AdaOnboardingStore alloc] initWithDefaults:defaults].state.step != AdaWelcome) return NO;
    bad[@"step"] = @3; bad[@"speaker_played"] = @0;
    if ([[AdaOnboardingStore alloc] initWithDefaults:defaults].state.step != AdaWelcome) return NO;
    NSDictionary *session = @{@"schema": @"adaos.ios.client-session.v1", @"zone": @"ru", @"subnet_id": @"synthetic-test",
        @"webspace_id": @"desktop", @"endpoint_id": @"ios-synthetic-test", @"session_jwt": @"synthetic.not-a-credential.signature"};
    if (![AdaSubnetSession valid:session]) return NO; /* Shape only; no authentication or network. */
    NSDictionary *reply = @{@"id": @"reply-1", @"from": @"hub", @"text": @"Synthetic reply",
        @"webspace_id": @"desktop", @"dialog_channel_id": @"conversational",
        @"voice_output_owner": @{@"kind": @"desktop", @"endpoint_id": @"ios-synthetic-test"}};
    NSDictionary *entry = [AdaSubnetClient chatEntryFromPayload:reply session:session];
    if (![entry[@"from"] isEqual:@"assistant"] || ![entry[@"automatic_speech"] boolValue]) return NO;
    NSMutableDictionary *other = [reply mutableCopy]; other[@"webspace_id"] = @"another-space";
    if ([AdaSubnetClient chatEntryFromPayload:other session:session]) return NO;
    other = [reply mutableCopy]; other[@"dialog_channel_id"] = @"another-channel";
    if ([AdaSubnetClient chatEntryFromPayload:other session:session]) return NO;
    other = [reply mutableCopy]; [other removeObjectForKey:@"id"]; other[@"ts"] = @1234567890;
    NSDictionary *unkeyed = [AdaSubnetClient chatEntryFromPayload:other session:session];
    if (![unkeyed[@"id"] hasPrefix:@"unkeyed-"] ||
        ![unkeyed[@"id"] isEqual:[AdaSubnetClient chatEntryFromPayload:other session:session][@"id"]]) return NO;
    other[@"ts"] = @1234567891;
    if ([unkeyed[@"id"] isEqual:[AdaSubnetClient chatEntryFromPayload:other session:session][@"id"]]) return NO;
    other = [reply mutableCopy]; other[@"voice_output_owner"] = @{@"kind": @"desktop", @"endpoint_id": @"another-device"};
    if ([[AdaSubnetClient chatEntryFromPayload:other session:session][@"automatic_speech"] boolValue]) return NO;
    other = [reply mutableCopy]; other[@"from"] = @"user";
    other[@"_meta"] = @{@"client_message_id": @"optimistic-turn"};
    if (![[AdaSubnetClient chatEntryFromPayload:other session:session][@"id"] isEqual:@"optimistic-turn"]) return NO;
    for (NSString *key in @[@"subnet_id", @"webspace_id", @"endpoint_id"]) {
        NSMutableDictionary *invalid = [session mutableCopy]; invalid[key] = @"../other-target";
        if ([AdaSubnetSession valid:invalid]) return NO;
        invalid[key] = @123; if ([AdaSubnetSession valid:invalid]) return NO;
    }
    NSMutableDictionary *invalid = [session mutableCopy]; invalid[@"zone"] = @"https://untrusted.invalid";
    if ([AdaSubnetSession valid:invalid]) return NO;
    invalid = [session mutableCopy]; invalid[@"session_jwt"] = @"value\nAuthorization: other";
    if ([AdaSubnetSession valid:invalid]) return NO;
    return YES;
}
