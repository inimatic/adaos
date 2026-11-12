#import "AdaClientSelfTest.h"
#import "AdaOnboardingStore.h"
#import "AdaSubnetSession.h"
#import "AdaSubnetClient.h"
#import "AdaClientUIProbe.h"
#import "AdaLocalUIProbe.h"
#import "AdaVoiceActivation.h"
#import "AdaVoiceSetupStore.h"
#import "AdaVoiceGate.h"
#import "AdaVoiceSegmentBuffer.h"
#import "LabAudioProbe.h"

@interface AdaSubnetClient (ContractTimer)
- (void)tick:(NSTimer *)timer;
@end

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

/* No WebKit, sockets, Keychain or actual preferences in transport checks. */
@interface AdaMemoryTransport : NSObject
@property(nonatomic, copy) void (^event)(NSString *, NSDictionary *);
@property(nonatomic, strong) NSMutableArray *frames;
@end
@implementation AdaMemoryTransport
- (instancetype)init { self = [super init]; if (self) self.frames = [NSMutableArray array]; return self; }
- (void)send:(NSDictionary *)frame completion:(void (^)(BOOL))completion { [self.frames addObject:frame]; if (completion) completion(YES); }
- (void)stop {}
@end

BOOL AdaClientContractSelfTest(void) {
    AdaVoiceSegmentBuffer *buffer = [[AdaVoiceSegmentBuffer alloc] init];
    NSMutableData *first = [NSMutableData dataWithLength:32000];
    if (![buffer append:first followup:YES now:100]) return NO;
    ((uint8_t *)first.mutableBytes)[0] = 42; /* The backlog must own an immutable copy. */
    if (![buffer append:first followup:NO now:101] || [buffer append:first followup:NO now:102] || buffer.count != 2 || buffer.dropped != 1) return NO;
    NSDictionary *head = [buffer take:105.9];
    if (![head[@"followup"] boolValue] || ((const uint8_t *)[head[@"pcm"] bytes])[0] != 0) return NO;
    if ([buffer take:107] || buffer.count || buffer.dropped != 2) return NO;
    if (![buffer append:first followup:YES now:200]) return NO;
    [buffer discard]; if ([buffer take:200] || buffer.dropped != 3) return NO;
    [buffer reset];
    if (buffer.count || buffer.dropped || [buffer append:[NSMutableData dataWithLength:384000] followup:NO now:1]) return NO;
    AdaVoiceGate gate = {0}; AdaVoiceGateArm(&gate,100); AdaVoiceGateBegin(&gate,105.9);
    if (!AdaVoiceGateTake(&gate) || AdaVoiceGateTake(&gate)) return NO;
    AdaVoiceGateArm(&gate,100); AdaVoiceGateBegin(&gate,106);
    if (AdaVoiceGateTake(&gate)) return NO;
    NSData *silence = [LabAudioProbe oneSecondSilenceWAV];
    if ([AdaVoiceActivation pcmFromWAV:silence].length != 32000 ||
        [AdaVoiceActivation pcmFromWAV:[silence subdataWithRange:NSMakeRange(0, silence.length-1)]]) return NO;
    if (![AdaVoiceActivation addressed:@"Арсений, проверка связи"] ||
        [AdaVoiceActivation addressed:@"Сегодня Арсений проверяет связь"] ||
        [AdaVoiceActivation addressed:@"Арсений"] || [AdaVoiceActivation addressed:@"Арсенийка проверь связь"]) return NO;
    NSArray *targets = @[@{@"id": @"test-agent", @"label": @"Арсений", @"aliases": @[@"арсений"]}];
    if (![[AdaVoiceActivation targetFromDescriptor:targets] isEqual:@"test-agent"] ||
        [AdaVoiceActivation targetFromDescriptor:[targets arrayByAddingObject:@{@"id": @"other", @"label": @"Арсений"}]]) return NO;
    if (![AdaLocalUIProbe validJob:@{@"operation": @"keyboard-and-speech-check", @"expires_at": @1050} now:1000] ||
        [AdaLocalUIProbe validJob:@{@"operation": @"keyboard-and-speech-check", @"expires_at": @999} now:1000] ||
        [AdaLocalUIProbe validJob:@{@"operation": @"keyboard-and-speech-check", @"expires_at": @1201} now:1000] ||
        [AdaLocalUIProbe validJob:@{@"operation": @"record-audio", @"expires_at": @1050} now:1000]) return NO;
    if (![AdaClientUIProbe validJob:@{@"operation": @"conversational-reply-speech-check", @"expires_at": @1050} now:1000] ||
        ![AdaClientUIProbe validJob:@{@"operation": @"conversational-text-check", @"expires_at": @1050} now:1000] ||
        [AdaClientUIProbe validJob:@{@"operation": @"conversational-text-check", @"expires_at": @999} now:1000] ||
        [AdaClientUIProbe validJob:@{@"operation": @"conversational-text-check", @"expires_at": @1200} now:1000] ||
        [AdaClientUIProbe validJob:@{@"operation": @"arbitrary-command", @"expires_at": @1050} now:1000]) return NO;
    AdaMemoryDefaults *defaults = [[AdaMemoryDefaults alloc] init];
    AdaVoiceSetupStore *voice = [[AdaVoiceSetupStore alloc] initWithDefaults:defaults];
    [voice begin]; [voice confirmHeard:YES];
    if ([voice.report[@"ready"] boolValue] || [voice.report[@"heard"] boolValue]) return NO;
    double now = [NSDate date].timeIntervalSince1970;
    NSDictionary *activation = @{@"updated_at": @(now), @"captured_bytes": @32000, @"recognized": @1,
        @"ready_cues": @1, @"followup_commands": @1, @"own_replies": @1};
    [voice observe:activation speech:@{@"state": @"completed", @"updated_at": @(now-100)}];
    if ([voice.report[@"synthesis"] boolValue]) return NO;
    [voice observe:activation speech:@{@"state": @"completed", @"updated_at": @(now)}];
    if ([voice.report[@"ready"] boolValue]) return NO; /* Callback is not human hearing. */
    [voice confirmHeard:NO]; if ([voice.report[@"ready"] boolValue]) return NO;
    [voice confirmHeard:YES]; if (![voice.report[@"ready"] boolValue]) return NO;
    voice.enabled = YES; [voice finish];
    voice = [[AdaVoiceSetupStore alloc] initWithDefaults:defaults];
    if (!voice.enabled || ![voice.report[@"ready"] boolValue]) return NO;
    [voice begin]; if ([voice.report[@"heard"] boolValue] || [voice.report[@"finished"] boolValue]) return NO;
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
    AdaMemoryTransport *wire = [[AdaMemoryTransport alloc] init];
    AdaSubnetClient *client = [[AdaSubnetClient alloc] initWithTransport:(AdaWebIOTransport *)(id)wire];
    [client setValue:session forKey:@"session"]; [client setValue:@YES forKey:@"foreground"]; [client setValue:@YES forKey:@"ready"];
    if (![client sendText:@"Synthetic message"] || !client.waitingForReply || wire.frames.count != 1) return NO;
    __block BOOL lateCallback = NO;
    if (![client requestVoiceKind:@"voice.transcribe" payload:@{} completion:^(NSDictionary *data, BOOL ok) { (void)data; (void)ok; lateCallback = YES; }]) return NO;
    NSDictionary *late = wire.frames.lastObject;
    wire.event(@"closed",nil);
    if (client.ready || client.waitingForReply || ![client.messages.lastObject[@"delivery"] isEqual:@"unknown"]) return NO;
    [client setValue:@YES forKey:@"ready"];
    wire.event(@"message",@{@"ch": @"events", @"t": @"ack", @"id": late[@"id"], @"ok": @YES, @"data": @{@"text": @"Арсений синтетическая команда"}});
    if (lateCallback || wire.frames.count != 2) return NO; /* Lost requests are not replayed. */
    [client setValue:@YES forKey:@"voiceReady"];
    if (![client sendVoice:silence russian:YES] || !client.voicePending) return NO;
    NSDictionary *audioFrame = wire.frames.lastObject; [client cancelVoiceInput];
    if (!client.ready || client.voicePending) return NO; /* Local Stop keeps text connection. */
    wire.event(@"message",@{@"ch": @"events", @"t": @"ack", @"id": audioFrame[@"id"], @"ok": @YES, @"data": @{@"text": @"Late synthetic transcript"}});
    if (client.messages.count != 1 || wire.frames.count != 3) return NO;
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
    /* Retry only read-only capability discovery after a temporary failure. */
    AdaMemoryTransport *availabilityWire = [[AdaMemoryTransport alloc] init];
    AdaSubnetClient *availability = [[AdaSubnetClient alloc] initWithTransport:(AdaWebIOTransport *)(id)availabilityWire];
    [availability setValue:session forKey:@"session"]; [availability setValue:@YES forKey:@"foreground"];
    [availability setValue:@([NSDate date].timeIntervalSince1970) forKey:@"lastPing"];
    availabilityWire.event(@"message",@{@"ch": @"events", @"t": @"ack", @"id": @"device.register.ios", @"ok": @YES});
    if (availabilityWire.frames.count != 1) return NO;
    NSDictionary *check = availabilityWire.frames.lastObject;
    availabilityWire.event(@"message",@{@"ch": @"events", @"t": @"ack", @"id": check[@"id"], @"ok": @NO});
    [availability tick:nil]; if (availabilityWire.frames.count != 1 || availability.voiceReady) return NO;
    [availability setValue:@0 forKey:@"voiceRetryAt"]; [availability tick:nil]; [availability tick:nil];
    if (availabilityWire.frames.count != 2 || availability.voiceChecked) return NO;
    check = availabilityWire.frames.lastObject;
    if (![check[@"kind"] isEqual:@"voice.activation.describe"]) return NO;
    availabilityWire.event(@"message",@{@"ch": @"events", @"t": @"ack", @"id": check[@"id"], @"ok": @YES,
        @"data": @{@"contract": @"adaos.voice.activation.v1", @"targets": targets}});
    if (!availability.voiceReady || !availability.voiceChecked) return NO;
    availabilityWire.event(@"closed",@{@"code": @4403}); [availability tick:nil];
    if (availability.ready || availabilityWire.frames.count != 2 || ![availability.status isEqual:@"authorization_required"]) return NO;
    return YES;
}
