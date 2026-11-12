#import "AdaSubnetClient.h"
#import "AdaSubnetSession.h"
#import "AdaJSONRequest.h"
#import <Security/Security.h>
#import <math.h>
#import <CommonCrypto/CommonDigest.h>

static BOOL IsTrue(id value) {
    return [value isKindOfClass:[NSNumber class]] && CFGetTypeID((__bridge CFTypeRef)value) == CFBooleanGetTypeID() && [value boolValue];
}
static NSString *ShortText(id value, NSUInteger limit) {
    return [value isKindOfClass:[NSString class]] && [value length] <= limit ? value : @"";
}
@interface AdaSubnetClient ()
@property(nonatomic, readwrite, copy) NSString *status;
@property(nonatomic, readwrite, copy) NSString *detail;
@property(nonatomic, readwrite, strong) NSURL *pairURL;
@property(nonatomic, readwrite, strong) NSDate *pairExpiry;
@property(nonatomic, readwrite, strong) NSArray *messages;
@property(nonatomic, readwrite) BOOL ready;
@property(nonatomic, readwrite) BOOL voicePending;
@property(nonatomic, readwrite) BOOL waitingForReply;
@property(nonatomic) NSTimeInterval replyDeadline;
@property(nonatomic, readwrite) BOOL voiceReady;
@property(nonatomic, readwrite) BOOL voiceChecked;
@property(nonatomic, readwrite, strong) NSArray *activationTargets;
@property(nonatomic, strong) NSMutableDictionary *voiceCallbacks;
@property(nonatomic, readwrite) NSUInteger receivedFrames;
@property(nonatomic, readwrite) NSUInteger receivedChatFrames;
@property(nonatomic, readwrite) NSUInteger acceptedChatFrames;
@property(nonatomic, readwrite, copy) NSString *lastChatSender;
@property(nonatomic, readwrite, strong) AdaWebIOTransport *transport;
@property(nonatomic, strong) AdaJSONRequest *http;
@property(nonatomic, strong) NSDictionary *session;
@property(nonatomic, copy) NSString *zone;
@property(nonatomic, copy) NSString *pairCode;
@property(nonatomic, strong) NSTimer *timer;
@property(nonatomic, strong) NSMutableDictionary *pending;
@property(nonatomic) NSUInteger generation;
@property(nonatomic) BOOL foreground;
@property(nonatomic) NSTimeInterval connectStarted;
@property(nonatomic) NSTimeInterval lastPing;
@property(nonatomic) NSTimeInterval voiceRetryAt;
@property(nonatomic) NSUInteger voiceCheckFailures;
- (void)checkVoiceAvailability;
- (void)voiceAvailabilityFailed;
@end
@implementation AdaSubnetClient
- (instancetype)init {
    return [self initWithTransport:nil];
}
- (instancetype)initWithTransport:(AdaWebIOTransport *)transport {
    self = [super init];
    if (self) {
        self.status = @"disconnected"; self.detail = @""; self.messages = @[];
        self.http = [[AdaJSONRequest alloc] init]; self.pending = [NSMutableDictionary dictionary];
        self.voiceCallbacks = [NSMutableDictionary dictionary]; self.activationTargets = @[];
        self.transport = transport ?: [[AdaWebIOTransport alloc] init];
        __weak AdaSubnetClient *weakSelf = self;
        self.transport.event = ^(NSString *kind, NSDictionary *message) { [weakSelf transportEvent:kind message:message]; };
    }
    return self;
}
- (void)notify { if (self.changed) self.changed(); }
- (void)setStatusValue:(NSString *)status detail:(NSString *)detail {
    self.status = status; self.detail = detail ?: @""; [self notify];
}
- (NSString *)origin { return [self.zone isEqual:@"ru"] ? @"https://ru.api.inimatic.com" : @"https://api.inimatic.com"; }
- (void)startTimer {
    [self.timer invalidate];
    self.timer = [NSTimer scheduledTimerWithTimeInterval:2 target:self selector:@selector(tick:) userInfo:nil repeats:YES];
}
- (void)start {
    self.foreground = YES;
    NSInteger status;
    self.session = [AdaSubnetSession read:&status];
    if (self.session) { self.zone = self.session[@"zone"]; [self connect]; }
    else if (status != errSecItemNotFound) [self setStatusValue:@"storage_error" detail:[NSString stringWithFormat:@"keychain_%ld", (long)status]];
    else if (self.pairCode && self.pairExpiry.timeIntervalSinceNow > 0) { [self startTimer]; [self pollPairing]; }
    else { [self cancelPairing]; [self setStatusValue:@"disconnected" detail:nil]; }
}
- (void)pairInZone:(NSString *)zone {
    if (!self.foreground || self.session || ![@[@"ru", @"global"] containsObject:zone]) return;
    [self cancelPairing]; self.zone = zone;
    NSUInteger generation = self.generation;
    [self setStatusValue:@"creating_pair" detail:nil];
    NSURL *url = [NSURL URLWithString:[[self origin] stringByAppendingString:@"/v1/browser/pair/create"]];
    [self.http sendURL:url method:@"POST" body:@{@"ttl": @600, @"zone_id": zone} bearer:nil completion:^(NSDictionary *value, NSString *error) {
        if (generation != self.generation || !self.foreground) return;
        id expiry = value[@"expires_at"];
        double seconds = [expiry isKindOfClass:[NSNumber class]] ? [expiry doubleValue] : 0;
        if (error || !IsTrue(value[@"ok"]) || ![AdaSubnetSession safeID:value[@"pair_code"]] ||
            !isfinite(seconds) || seconds <= [NSDate date].timeIntervalSince1970 || seconds > [NSDate date].timeIntervalSince1970 + 900) {
            [self setStatusValue:@"error" detail:error ?: @"invalid_pairing_response"]; return;
        }
        self.pairCode = value[@"pair_code"]; self.pairExpiry = [NSDate dateWithTimeIntervalSince1970:seconds];
        NSURLComponents *link = [NSURLComponents componentsWithString:@"https://inimatic.com/"];
        link.queryItems = @[[NSURLQueryItem queryItemWithName:@"pair_code" value:self.pairCode], [NSURLQueryItem queryItemWithName:@"zone" value:self.zone]];
        self.pairURL = link.URL;
        [self setStatusValue:@"awaiting_approval" detail:nil]; [self startTimer];
    }];
}
- (void)cancelPairing {
    ++self.generation; [self.http cancel]; [self.timer invalidate]; self.timer = nil;
    self.pairCode = nil; self.pairURL = nil; self.pairExpiry = nil;
    if (!self.session) [self setStatusValue:@"disconnected" detail:nil];
}
- (void)pollPairing {
    if (!self.foreground || !self.pairCode || self.http.running) return;
    if (self.pairExpiry.timeIntervalSinceNow <= 0) {
        [self cancelPairing]; [self setStatusValue:@"pair_expired" detail:nil]; return;
    }
    NSURLComponents *url = [NSURLComponents componentsWithString:[[self origin] stringByAppendingString:@"/v1/browser/pair/status"]];
    url.queryItems = @[[NSURLQueryItem queryItemWithName:@"code" value:self.pairCode],
        [NSURLQueryItem queryItemWithName:@"zone_id" value:self.zone],
        [NSURLQueryItem queryItemWithName:@"_" value:[NSString stringWithFormat:@"%.0f", [NSDate date].timeIntervalSince1970 * 1000]]];
    NSUInteger generation = self.generation;
    [self.http sendURL:url.URL method:@"GET" body:nil bearer:nil completion:^(NSDictionary *value, NSString *error) {
        if (generation != self.generation || !self.foreground) return;
        if (error) { [self setStatusValue:@"awaiting_approval" detail:error]; return; }
        NSString *state = value[@"state"];
        if (IsTrue(value[@"ok"]) && [state isEqual:@"approved"]) {
            NSString *subnet = ShortText(value[@"subnet_id"], 128);
            if (!subnet.length) subnet = ShortText(value[@"hub_id"], 128);
            NSString *webspace = ShortText(value[@"webspace_id"], 128);
            if (!webspace.length) webspace = @"desktop"; /* Existing Seed Client contract. */
            NSDictionary *session = @{@"schema": @"adaos.ios.client-session.v1", @"zone": self.zone,
                @"subnet_id": subnet, @"webspace_id": webspace, @"endpoint_id": [AdaSubnetSession endpointID],
                @"session_jwt": ShortText(value[@"session_jwt"], 8192)};
            NSInteger status;
            if (![AdaSubnetSession save:session status:&status]) {
                [self.timer invalidate]; self.timer = nil;
                [self setStatusValue:@"storage_error" detail:[NSString stringWithFormat:@"keychain_%ld", (long)status]]; return;
            }
            self.session = session; [self cancelPairing]; [self connect];
        } else if (!IsTrue(value[@"ok"]) || [@[@"expired", @"revoked", @"not_found"] containsObject:state]) {
            [self cancelPairing]; [self setStatusValue:@"pair_rejected" detail:nil];
        } else [self setStatusValue:@"awaiting_approval" detail:nil];
    }];
}
- (void)connect {
    if (!self.foreground || ![AdaSubnetSession valid:self.session]) return;
    [self cancelActivationRequests];
    ++self.generation; [self.transport stop]; self.ready = NO; self.voiceReady = NO; self.voiceChecked = NO; self.activationTargets = @[];
    self.voiceCheckFailures = 0; self.voiceRetryAt = 0;
    NSURLComponents *url = [NSURLComponents componentsWithString:[self origin]];
    url.scheme = @"wss"; url.path = [NSString stringWithFormat:@"/hubs/%@/ws", self.session[@"subnet_id"]];
    url.queryItems = @[[NSURLQueryItem queryItemWithName:@"token" value:self.session[@"session_jwt"]]];
    self.connectStarted = [NSDate date].timeIntervalSince1970; self.lastPing = self.connectStarted;
    [self setStatusValue:@"connecting" detail:nil]; [self startTimer];
    [self.transport connectURL:url.URL];
}
- (void)tick:(NSTimer *)timer {
    (void)timer;
    if (!self.foreground) return;
    if (self.pairCode) { [self pollPairing]; [self notify]; return; }
    NSTimeInterval now = [NSDate date].timeIntervalSince1970;
    if (self.waitingForReply && now >= self.replyDeadline) {
        self.waitingForReply = NO; self.detail = @"reply_timeout_no_retry"; [self notify];
    }
    for (NSString *key in self.pending.allKeys) {
        NSDictionary *pending = self.pending[key];
        if ([pending[@"expires"] doubleValue] <= now) {
            void (^callback)(NSDictionary *, BOOL) = self.voiceCallbacks[key];
            if (callback) {
                [self.voiceCallbacks removeObjectForKey:key]; [self.pending removeObjectForKey:key];
                callback(@{}, NO); continue;
            }
            if ([pending[@"kind"] isEqual:@"voice.activation.describe"]) {
                [self voiceAvailabilityFailed];
                [self.pending removeObjectForKey:key]; [self notify]; continue;
            }
            if ([pending[@"kind"] isEqual:@"dialog.user_audio"]) self.voicePending = NO;
            [self updateDelivery:key state:@"unknown"]; [self.pending removeObjectForKey:key];
            self.detail = @"delivery_unknown_no_retry"; [self notify];
        }
    }
    if (self.ready && self.voiceChecked && !self.voiceReady && now >= self.voiceRetryAt) [self checkVoiceAvailability];
    if (!self.ready && [self.status isEqual:@"connecting"] && now - self.connectStarted > 20) {
        [self transportEvent:@"closed" message:nil]; return;
    }
    if (self.ready && now - self.lastPing > 25) {
        self.lastPing = now; [self.transport send:@{@"type": @"ping"} completion:nil];
    }
}
- (void)transportEvent:(NSString *)kind message:(NSDictionary *)message {
    if (!self.foreground) return;
    if ([kind isEqual:@"open"]) {
        [self.transport send:@{@"type": @"subscribe", @"topics": @[@"io.out.chat.append"]} completion:nil];
        NSString *version = [[NSBundle mainBundle] objectForInfoDictionaryKey:@"CFBundleShortVersionString"] ?: @"unknown";
        NSDictionary *payload = @{@"device_id": self.session[@"endpoint_id"], @"client_id": self.session[@"endpoint_id"],
            @"webspace_id": self.session[@"webspace_id"], @"device_display_name": @"AdaOS iOS",
            @"endpoint_display_name": @"AdaOS iOS", @"browser_family": @"AdaOS Native iOS", @"os_name": @"ios",
            @"form_factor": [self.diagnostics[@"platform"] objectForKey:@"form_factor"] ?: @"phone",
            @"client_build_version": version, @"client_build_id": @"adaos-ios-native",
            @"user_agent": [@"AdaOS Native iOS/" stringByAppendingString:version],
            @"client_sent_at_ms": @([NSDate date].timeIntervalSince1970 * 1000)};
        [self.transport send:@{@"ch": @"events", @"t": @"cmd", @"id": @"device.register.ios", @"kind": @"device.register", @"payload": payload} completion:nil];
    } else if ([kind isEqual:@"message"]) {
        [self receive:message];
    } else {
        self.ready = NO; self.voiceReady = NO; self.waitingForReply = NO; [self.transport stop];
        [self cancelActivationRequests];
        for (NSString *key in self.pending.allKeys) [self updateDelivery:key state:@"unknown"];
        [self.pending removeAllObjects]; self.voicePending = NO;
        [self.timer invalidate]; self.timer = nil;
        BOOL refused = [message[@"code"] isKindOfClass:[NSNumber class]] && [@[@1008, @4401, @4403] containsObject:message[@"code"]];
        [self setStatusValue:refused ? @"authorization_required" : @"offline" detail:refused ? @"websocket_access_refused" : @"reconnect_explicitly_no_message_retry"];
    }
}
- (void)updateDelivery:(NSString *)identifier state:(NSString *)state {
    NSMutableArray *messages = [self.messages mutableCopy];
    for (NSUInteger i = 0; i < messages.count; i++) if ([messages[i][@"id"] isEqual:identifier]) {
        NSMutableDictionary *entry = [messages[i] mutableCopy]; entry[@"delivery"] = state; messages[i] = entry;
    }
    self.messages = messages;
}
- (void)append:(NSDictionary *)entry {
    NSMutableArray *messages = [self.messages mutableCopy];
    for (NSUInteger i = 0; i < messages.count; i++) if ([messages[i][@"id"] isEqual:entry[@"id"]]) {
        messages[i] = entry; self.messages = messages; [self notify]; return;
    }
    [messages addObject:entry];
    if (messages.count > 100) [messages removeObjectAtIndex:0];
    self.messages = messages; [self notify];
}
- (void)voiceAvailabilityFailed {
    self.voiceReady = NO; self.voiceChecked = YES; self.activationTargets = @[];
    self.voiceCheckFailures = MIN(6, self.voiceCheckFailures + 1);
    self.voiceRetryAt = [NSDate date].timeIntervalSince1970 + MIN(30, 5 * self.voiceCheckFailures);
}
- (void)checkVoiceAvailability {
    if (!self.ready || !self.foreground) return;
    for (NSDictionary *pending in self.pending.allValues)
        if ([pending[@"kind"] isEqual:@"voice.activation.describe"]) return;
    self.voiceChecked = NO;
    BOOL ru = ![[self.diagnostics[@"locale"] description] isEqual:@"en"];
    if (![self sendKind:@"voice.activation.describe" payload:@{@"lang": ru ? @"ru-RU" : @"en-US"}]) [self voiceAvailabilityFailed];
    [self notify];
}
- (void)receive:(NSDictionary *)message {
    self.receivedFrames++;
    if (![message[@"ch"] isEqual:@"events"]) return;
    NSString *identifier = ShortText(message[@"id"], 128);
    if ([message[@"t"] isEqual:@"ack"]) {
        if ([identifier isEqual:@"device.register.ios"]) {
            self.ready = IsTrue(message[@"ok"]);
            [self setStatusValue:self.ready ? @"connected" : @"authorization_required" detail:self.ready ? nil : @"registration_refused"];
            if (!self.ready) { [self.transport stop]; [self.timer invalidate]; self.timer = nil; }
            else {
                [self checkVoiceAvailability];
            }
        } else if (self.pending[identifier]) {
            NSDictionary *pending = self.pending[identifier];
            BOOL ok = IsTrue(message[@"ok"]);
            if (!ok && [@[@"dialog.user_message", @"dialog.user_audio"] containsObject:pending[@"kind"]]) self.waitingForReply = NO;
            if ([pending[@"kind"] isEqual:@"voice.activation.describe"]) {
                NSDictionary *data = [message[@"data"] isKindOfClass:[NSDictionary class]] ? message[@"data"] : @{};
                self.voiceReady = ok && [data[@"contract"] isEqual:@"adaos.voice.activation.v1"] &&
                    [data[@"targets"] isKindOfClass:[NSArray class]] && [data[@"targets"] count] > 0;
                self.voiceChecked = YES;
                self.activationTargets = self.voiceReady ? data[@"targets"] : @[];
                if (self.voiceReady) self.voiceCheckFailures = 0;
                else [self voiceAvailabilityFailed];
            } else if ([pending[@"kind"] isEqual:@"dialog.user_audio"]) {
                self.voicePending = NO;
                NSDictionary *data = [message[@"data"] isKindOfClass:[NSDictionary class]] ? message[@"data"] : @{};
                NSString *text = ShortText(data[@"text"], 8192);
                if (ok && text.length) [self append:@{@"id": identifier, @"from": @"user", @"text": text, @"delivery": @"sent"}];
                else self.detail = @"voice_not_recognized_or_refused";
            } else if (self.voiceCallbacks[identifier]) {
                void (^callback)(NSDictionary *, BOOL) = self.voiceCallbacks[identifier];
                [self.voiceCallbacks removeObjectForKey:identifier];
                [self.pending removeObjectForKey:identifier];
                callback([message[@"data"] isKindOfClass:[NSDictionary class]] ? message[@"data"] : @{}, ok);
            } else [self updateDelivery:identifier state:ok ? @"sent" : @"refused"];
            [self.pending removeObjectForKey:identifier]; [self notify];
        }
        return;
    }
    if (![message[@"t"] isEqual:@"evt"] || ![message[@"kind"] isEqual:@"io.out.chat.append"]) return;
    NSDictionary *payload = [message[@"payload"] isKindOfClass:[NSDictionary class]] ? message[@"payload"] : nil;
    if (!payload) return;
    self.receivedChatFrames++;
    self.lastChatSender = [@[@"user", @"assistant", @"hub"] containsObject:payload[@"from"]] ? payload[@"from"] : @"other";
    NSDictionary *entry = [AdaSubnetClient chatEntryFromPayload:payload session:self.session];
    if (entry) {
        if ([entry[@"from"] isEqual:@"assistant"] && [entry[@"automatic_speech"] boolValue]) self.waitingForReply = NO;
        self.acceptedChatFrames++; [self append:entry];
    }
    else [self notify];
}
+ (NSDictionary *)chatEntryFromPayload:(NSDictionary *)payload session:(NSDictionary *)session {
    if (![payload isKindOfClass:[NSDictionary class]] || ![AdaSubnetSession valid:session]) return nil;
    NSDictionary *meta = [payload[@"_meta"] isKindOfClass:[NSDictionary class]] ? payload[@"_meta"] : @{};
    NSString *webspace = ShortText(payload[@"webspace_id"], 128);
    if (!webspace.length) webspace = ShortText(meta[@"webspace_id"], 128);
    NSString *channel = ShortText(payload[@"dialog_channel_id"], 128);
    if (!channel.length) channel = ShortText(meta[@"dialog_channel_id"], 128);
    if ((webspace.length && ![webspace isEqual:session[@"webspace_id"]]) ||
        (channel.length && ![channel isEqual:@"conversational"])) return nil;
    NSString *text = ShortText(payload[@"text"], 8192), *from = ShortText(payload[@"from"], 32);
    if (!text.length || ![@[@"user", @"assistant", @"hub"] containsObject:from]) return nil;
    if ([from isEqual:@"hub"]) from = @"assistant"; /* Core router's default reply sender. */
    NSString *messageID = ShortText(payload[@"id"], 128);
    if ([from isEqual:@"user"] && ShortText(meta[@"client_message_id"], 128).length) messageID = meta[@"client_message_id"];
    if (!messageID.length) {
        /* SDK chat_append permits an empty id. Use a presentation-only fingerprint
         * for repeated copies with the same timestamp, never a command identity. */
        id timestamp = payload[@"ts"];
        if (![timestamp isKindOfClass:[NSNumber class]] || !isfinite([timestamp doubleValue])) timestamp = [NSUUID UUID].UUIDString;
        NSData *identity = [NSJSONSerialization dataWithJSONObject:@[session[@"webspace_id"], channel, from, timestamp, text] options:0 error:nil];
        if (!identity) return nil;
        unsigned char digest[CC_SHA256_DIGEST_LENGTH];
        CC_SHA256(identity.bytes, (CC_LONG)identity.length, digest);
        NSMutableString *fingerprint = [NSMutableString stringWithString:@"unkeyed-"];
        for (NSUInteger i = 0; i < sizeof(digest); i++) [fingerprint appendFormat:@"%02x", digest[i]];
        messageID = fingerprint;
    }
    NSDictionary *owner = [payload[@"voice_output_owner"] isKindOfClass:[NSDictionary class]] ? payload[@"voice_output_owner"] :
        ([meta[@"voice_output_owner"] isKindOfClass:[NSDictionary class]] ? meta[@"voice_output_owner"] : @{});
    BOOL ownOutput = [owner[@"kind"] isEqual:@"desktop"] && [owner[@"endpoint_id"] isEqual:session[@"endpoint_id"]];
    NSString *sender = ShortText(payload[@"active_agent_label"], 128);
    if (!sender.length) sender = ShortText(meta[@"active_agent_label"], 128);
    return @{@"id": messageID, @"from": from, @"text": text, @"delivery": @"sent",
        @"automatic_speech": @(ownOutput), @"sender": sender.length ? sender : @"AdaOS"};
}
- (NSString *)sendKind:(NSString *)kind payload:(NSDictionary *)payload {
    if (!self.ready || !self.foreground || self.pending.count >= 16) return nil;
    NSString *identifier = [@"ios-" stringByAppendingString:[NSUUID UUID].UUIDString.lowercaseString];
    NSMutableDictionary *body = [payload mutableCopy];
    NSDictionary *owner = @{@"kind": @"desktop", @"endpoint_id": self.session[@"endpoint_id"], @"label": @"AdaOS iOS"};
    body[@"webspace_id"] = self.session[@"webspace_id"];
    NSMutableDictionary *meta = [@{@"dialog_channel_id": @"conversational", @"voice_chat_scope": @"shared",
        @"client_message_id": identifier, @"client_kind": @"adaos_ios_native", @"voice_output_owner": owner,
        @"voice_input_owner": owner, @"voice_listening_mode": @"push-to-talk", @"capture_backend": @"adaos_ios_native"} mutableCopy];
    if (IsTrue(payload[@"voice_activation"])) {
        meta[@"voice_listening_mode"] = @"activation"; meta[@"voice_activation"] = @YES;
        meta[@"voice_activation_alias"] = @"Арсений";
        meta[@"addressed_agent_id"] = payload[@"addressed_agent_id"] ?: @"";
    }
    body[@"_meta"] = meta;
    NSTimeInterval wait = [@[@"dialog.user_audio", @"voice.transcribe"] containsObject:kind] ? 45 : 10;
    self.pending[identifier] = @{@"kind": kind, @"expires": @([NSDate date].timeIntervalSince1970 + wait)};
    [self.transport send:@{@"ch": @"events", @"t": @"cmd", @"id": identifier, @"kind": kind, @"payload": body} completion:^(BOOL sent) {
        if (!sent && self.pending[identifier]) {
            void (^callback)(NSDictionary *, BOOL) = self.voiceCallbacks[identifier];
            [self.voiceCallbacks removeObjectForKey:identifier];
            [self.pending removeObjectForKey:identifier]; [self updateDelivery:identifier state:@"not_sent"];
            if ([kind isEqual:@"dialog.user_audio"]) self.voicePending = NO;
            if ([kind isEqual:@"voice.activation.describe"]) [self voiceAvailabilityFailed];
            if ([@[@"dialog.user_message", @"dialog.user_audio"] containsObject:kind]) self.waitingForReply = NO;
            self.detail = @"send_failed_no_retry"; [self notify];
            if (callback) callback(@{}, NO);
        }
    }];
    return identifier;
}
- (BOOL)sendText:(NSString *)text {
    text = [text stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (!text.length || text.length > 4000) return NO;
    NSString *identifier = [self sendKind:@"dialog.user_message" payload:@{@"text": text}];
    if (!identifier) return NO;
    self.waitingForReply = YES; self.replyDeadline = [NSDate date].timeIntervalSince1970 + 45;
    [self append:@{@"id": identifier, @"from": @"user", @"text": text, @"delivery": @"sending"}];
    return YES;
}
- (BOOL)sendVoice:(NSData *)wav russian:(BOOL)russian {
    if (!self.voiceReady || self.voicePending || wav.length < 44 || wav.length > 128 * 1024) return NO;
    self.voicePending = YES;
    NSString *identifier = [self sendKind:@"dialog.user_audio" payload:@{@"audio_b64": [wav base64EncodedStringWithOptions:0],
        @"lang": russian ? @"ru-RU" : @"en-US", @"duration_ms": @3000}];
    if (!identifier) self.voicePending = NO;
    else { self.waitingForReply = YES; self.replyDeadline = [NSDate date].timeIntervalSince1970 + 60; }
    [self notify]; return identifier != nil;
}
- (BOOL)sendActivatedText:(NSString *)text target:(NSString *)target {
    if (!text.length || text.length > 4000 || !target.length) return NO;
    NSString *identifier = [self sendKind:@"dialog.user_message" payload:@{@"text": text,
        @"voice_activation": @YES, @"addressed_agent_id": target}];
    if (!identifier) return NO;
    self.waitingForReply = YES; self.replyDeadline = [NSDate date].timeIntervalSince1970 + 45;
    [self append:@{@"id": identifier, @"from": @"user", @"text": text, @"delivery": @"sending"}];
    return YES;
}
- (BOOL)requestVoiceKind:(NSString *)kind payload:(NSDictionary *)payload completion:(void (^)(NSDictionary *, BOOL))completion {
    if (![@[@"voice.transcribe", @"voice.activation.claim"] containsObject:kind] || !completion) return NO;
    NSString *identifier = [self sendKind:kind payload:payload];
    if (!identifier) return NO;
    self.voiceCallbacks[identifier] = [completion copy]; return YES;
}
- (void)cancelActivationRequests {
    for (NSString *key in self.voiceCallbacks.allKeys) [self.pending removeObjectForKey:key];
    [self.voiceCallbacks removeAllObjects]; /* Late ACKs cannot dispatch a cancelled utterance. */
}
- (void)cancelVoiceInput {
    /* Only local waiting. The already submitted node operation is not cancelled. */
    for (NSString *key in self.pending.allKeys) if ([self.pending[key][@"kind"] isEqual:@"dialog.user_audio"]) {
        [self.pending removeObjectForKey:key];
    }
    self.voicePending = NO; [self notify];
}
- (void)suspend {
    [self cancelActivationRequests];
    self.foreground = NO; ++self.generation;
    [self.http cancel]; [self.transport stop]; [self.timer invalidate]; self.timer = nil;
    self.ready = NO; self.voicePending = NO; self.waitingForReply = NO;
    for (NSString *key in self.pending.allKeys) [self updateDelivery:key state:@"unknown"];
    [self.pending removeAllObjects]; [self setStatusValue:@"suspended" detail:nil];
}
- (void)forget {
    [self suspend]; self.pairCode = nil; self.pairURL = nil; self.pairExpiry = nil;
    NSInteger status;
    if ([AdaSubnetSession forget:&status]) {
        self.session = nil; self.messages = @[]; self.foreground = YES;
        [self setStatusValue:@"disconnected" detail:nil];
    } else [self setStatusValue:@"storage_error" detail:[NSString stringWithFormat:@"keychain_%ld", (long)status]];
}
- (void)dealloc { [self.timer invalidate]; [self.http cancel]; [self.transport stop]; }
@end
