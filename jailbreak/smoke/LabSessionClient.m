#import "LabSessionClient.h"
#import <Security/Security.h>
#import <dispatch/dispatch.h>
#import <math.h>

static NSString *const ProfilePath = @"/var/mobile/Library/Application Support/AdaOSLab/pairing.json";
static const NSUInteger BodyLimit = 65536;

static BOOL TrueBoolean(id value) {
    return [value isKindOfClass:[NSNumber class]] &&
        CFGetTypeID((__bridge CFTypeRef)value) == CFBooleanGetTypeID() && [value boolValue];
}

static BOOL SafeID(id value, NSUInteger limit) {
    if (![value isKindOfClass:[NSString class]] || [value length] == 0 || [value length] > limit) return NO;
    NSCharacterSet *allowed = [NSCharacterSet characterSetWithCharactersInString:
        @"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"];
    return [value rangeOfCharacterFromSet:allowed.invertedSet].location == NSNotFound;
}

static BOOL ValidTarget(NSDictionary *value) {
    if (![value isKindOfClass:[NSDictionary class]]) return NO;
    return ([@"https://ru.api.inimatic.com" isEqual:value[@"root_origin"]] ||
            [@"https://api.inimatic.com" isEqual:value[@"root_origin"]]) &&
        SafeID(value[@"hub_id"], 128) && SafeID(value[@"webspace_id"], 128);
}

static BOOL ValidSession(NSDictionary *value) {
    if (!ValidTarget(value) || !SafeID(value[@"session_id"], 128)) return NO;
    NSString *token = value[@"access_token"];
    if (![token isKindOfClass:[NSString class]]) return NO;
    NSString *prefix = [NSString stringWithFormat:@"adaos-desktop-v1.%@.", value[@"session_id"]];
    if (![token hasPrefix:prefix] || token.length != prefix.length + 43 ||
        !SafeID([token substringFromIndex:prefix.length], 43)) return NO;
    id expiry = value[@"expires_at"];
    return [expiry isKindOfClass:[NSNumber class]] &&
        CFGetTypeID((__bridge CFTypeRef)expiry) != CFBooleanGetTypeID() &&
        isfinite([expiry doubleValue]) && [expiry doubleValue] > 0 &&
        [expiry doubleValue] < 253402300800.0;
}

static NSDictionary *SessionQuery(void) {
    return @{(__bridge id)kSecClass: (__bridge id)kSecClassGenericPassword,
             (__bridge id)kSecAttrService: @"com.inimatic.adaos.lab.session",
             (__bridge id)kSecAttrAccount: @"active-test-session"};
}

static NSDictionary *ReadSession(OSStatus *status) {
    NSMutableDictionary *query = [SessionQuery() mutableCopy];
    query[(__bridge id)kSecReturnData] = @YES;
    query[(__bridge id)kSecMatchLimit] = (__bridge id)kSecMatchLimitOne;
    CFTypeRef found = NULL;
    *status = SecItemCopyMatching((__bridge CFDictionaryRef)query, &found);
    id data = CFBridgingRelease(found);
    if (*status != errSecSuccess) return nil;
    if (![data isKindOfClass:[NSData class]] || [data length] > 8192) {
        *status = errSecDecode;
        return nil;
    }
    id value = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    if (!ValidSession(value)) {
        *status = errSecDecode;
        return nil;
    }
    return value;
}

static OSStatus WriteSession(NSDictionary *value) {
    if (!ValidSession(value)) return errSecParam;
    NSData *data = [NSJSONSerialization dataWithJSONObject:value options:0 error:nil];
    if (!data || data.length > 8192) return errSecParam;
    NSMutableDictionary *item = [SessionQuery() mutableCopy];
    item[(__bridge id)kSecValueData] = data;
    item[(__bridge id)kSecAttrAccessible] = (__bridge id)kSecAttrAccessibleWhenUnlockedThisDeviceOnly;
    // Do not replace any existing session implicitly.
    OSStatus status = SecItemAdd((__bridge CFDictionaryRef)item, NULL);
    if (status != errSecSuccess) return status;
    NSMutableDictionary *lookup = [SessionQuery() mutableCopy];
    lookup[(__bridge id)kSecReturnData] = @YES;
    lookup[(__bridge id)kSecMatchLimit] = (__bridge id)kSecMatchLimitOne;
    CFTypeRef found = NULL;
    status = SecItemCopyMatching((__bridge CFDictionaryRef)lookup, &found);
    id readBack = CFBridgingRelease(found);
    // Compare the persisted serialization, avoiding NSNumber JSON round-off.
    if (status == errSecSuccess && [readBack isKindOfClass:[NSData class]] && [readBack isEqualToData:data]) return errSecSuccess;
    SecItemDelete((__bridge CFDictionaryRef)SessionQuery());
    return status == errSecSuccess ? errSecDecode : status;
}

@interface LabSessionClient ()
@property(nonatomic, readwrite, getter=isRunning) BOOL running;
@property(nonatomic) NSUInteger generation;
@property(nonatomic) NSInteger operation;
@property(nonatomic, strong) dispatch_queue_t storageQueue;
@property(nonatomic, strong) NSURLSession *session;
@property(nonatomic, strong) NSURLSessionDataTask *task;
@property(nonatomic, strong) NSTimer *deadline;
@property(nonatomic, strong) NSMutableData *body;
@property(nonatomic, strong) NSDictionary *target;
@property(nonatomic, copy) LabSessionCompletion completion;
@property(nonatomic, copy) NSString *voiceRequestID;
@end

@implementation LabSessionClient
- (instancetype)init {
    self = [super init];
    if (self) _storageQueue = dispatch_queue_create("com.inimatic.adaos.lab.session-storage", DISPATCH_QUEUE_SERIAL);
    return self;
}

- (void)clearNetwork {
    [self.deadline invalidate];
    self.deadline = nil;
    [self.session invalidateAndCancel];
    self.session = nil;
    self.task = nil;
    self.body = nil;
}

- (void)finish:(NSString *)status {
    if (!self.running) return;
    self.running = NO;
    self.generation++;
    [self clearNetwork];
    self.target = nil;
    self.voiceRequestID = nil;
    LabSessionCompletion completion = self.completion;
    self.completion = nil;
    if (completion) completion(status);
}

- (void)cancel {
    [self finish:@"Session request cancelled. No automatic retry. A sent invitation claim may have reached the server."];
}

- (void)begin:(LabSessionCompletion)completion {
    NSAssert([NSThread isMainThread], @"Session controls use the main queue");
    [self cancel];
    self.running = YES;
    self.generation++;
    self.completion = completion;
}

- (void)loadForOperation:(NSInteger)operation voiceData:(NSData *)voiceData completion:(LabSessionCompletion)completion {
    [self begin:completion];
    BOOL pair = operation == 1;
    NSUInteger generation = self.generation;
    dispatch_async(self.storageQueue, ^{
        OSStatus storageStatus = errSecSuccess;
        NSDictionary *stored = ReadSession(&storageStatus);
        NSDictionary *profile = nil;
        if (pair && storageStatus == errSecItemNotFound) {
            NSDictionary *attributes = [[NSFileManager defaultManager] attributesOfItemAtPath:ProfilePath error:nil];
            if ([attributes[NSFileType] isEqual:NSFileTypeRegular] && [attributes[NSFileSize] unsignedLongLongValue] <= 4096) {
                NSData *data = [NSData dataWithContentsOfFile:ProfilePath];
                if (data) profile = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
            }
        }
        dispatch_async(dispatch_get_main_queue(), ^{
            if (!self.running || generation != self.generation) return;
            if (pair) {
                if (stored) {
                    [self finish:@"A session is already saved. Check access or forget it before pairing again."];
                } else if (storageStatus != errSecItemNotFound) {
                    [self finish:[NSString stringWithFormat:@"Secure storage unavailable (code %ld).", (long)storageStatus]];
                } else if (!ValidTarget(profile) || !SafeID(profile[@"invite_id"], 80) ||
                           ![profile[@"invite_id"] hasPrefix:@"desktop-"]) {
                    [self finish:@"No valid test invitation installed. Prepare a fresh invitation on the PC."];
                } else {
                    self.target = profile;
                    [self sendPath:[NSString stringWithFormat:@"/api/personalization/invites/%@/preview", profile[@"invite_id"]]
                              body:nil token:nil operation:1];
                }
            } else if (!stored) {
                [self finish:[NSString stringWithFormat:@"No readable saved session (code %ld).", (long)storageStatus]];
            } else if ([stored[@"expires_at"] doubleValue] <= [[NSDate date] timeIntervalSince1970]) {
                [self finish:@"Saved test session expired. Forget it before using a fresh invitation."];
            } else {
                self.target = stored;
                NSString *suffix = operation == 3 ? @"runtime" : operation == 4 ? @"voice/capabilities" : @"voice/transcribe";
                NSDictionary *body = nil;
                if (operation == 5) {
                    self.voiceRequestID = [[NSUUID UUID] UUIDString].lowercaseString;
                    body = @{@"request_id": self.voiceRequestID, @"created_at": @([[NSDate date] timeIntervalSince1970]),
                             @"audio_b64": [voiceData base64EncodedStringWithOptions:0], @"lang": @"ru-RU"};
                }
                [self sendPath:[NSString stringWithFormat:@"/api/node/yjs/webspaces/%@/%@", stored[@"webspace_id"], suffix]
                          body:body token:stored[@"access_token"] operation:operation];
            }
        });
    });
}

- (void)pairWithCompletion:(LabSessionCompletion)completion { [self loadForOperation:1 voiceData:nil completion:completion]; }
- (void)checkWithCompletion:(LabSessionCompletion)completion { [self loadForOperation:3 voiceData:nil completion:completion]; }
- (void)checkVoiceWithCompletion:(LabSessionCompletion)completion { [self loadForOperation:4 voiceData:nil completion:completion]; }
- (void)transcribeWAV:(NSData *)wav completion:(LabSessionCompletion)completion {
    if (![wav isKindOfClass:[NSData class]] || wav.length < 44 || wav.length > 131072) {
        completion(@"Voice sample outside the local WAV limit. Nothing sent."); return;
    }
    [self loadForOperation:5 voiceData:wav completion:completion];
}

- (void)forgetWithCompletion:(LabSessionCompletion)completion {
    [self begin:completion];
    NSUInteger generation = self.generation;
    dispatch_async(self.storageQueue, ^{
        OSStatus removed = SecItemDelete((__bridge CFDictionaryRef)SessionQuery());
        OSStatus checked = errSecSuccess;
        ReadSession(&checked);
        dispatch_async(dispatch_get_main_queue(), ^{
            if (!self.running || generation != self.generation) return;
            if ((removed == errSecSuccess || removed == errSecItemNotFound) && checked == errSecItemNotFound) {
                [self finish:@"Local session removed. Server access can be revoked on the PC and expires automatically."];
            } else {
                [self finish:[NSString stringWithFormat:@"Session removal failed (code %ld).", (long)removed]];
            }
        });
    });
}

- (void)sendPath:(NSString *)path body:(NSDictionary *)body token:(NSString *)token operation:(NSInteger)operation {
    [self clearNetwork];
    self.operation = operation;
    self.body = [NSMutableData data];
    NSURLSessionConfiguration *configuration = [NSURLSessionConfiguration ephemeralSessionConfiguration];
    configuration.URLCache = nil;
    configuration.HTTPCookieStorage = nil;
    configuration.URLCredentialStorage = nil;
    configuration.HTTPShouldSetCookies = NO;
    configuration.allowsCellularAccess = NO;
    configuration.timeoutIntervalForRequest = operation == 5 ? 20 : 10;
    configuration.timeoutIntervalForResource = operation == 5 ? 25 : 15;
    configuration.HTTPMaximumConnectionsPerHost = 1;
    self.session = [NSURLSession sessionWithConfiguration:configuration delegate:self delegateQueue:[NSOperationQueue mainQueue]];
    NSString *url = [NSString stringWithFormat:@"%@/hubs/%@%@", self.target[@"root_origin"], self.target[@"hub_id"], path];
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:url]
        cachePolicy:NSURLRequestReloadIgnoringLocalCacheData timeoutInterval:operation == 5 ? 20 : 10];
    request.HTTPMethod = body ? @"POST" : @"GET";
    request.HTTPShouldHandleCookies = NO;
    [request setValue:@"application/json" forHTTPHeaderField:@"Accept"];
    NSString *version = [[NSBundle mainBundle] objectForInfoDictionaryKey:@"CFBundleShortVersionString"];
    [request setValue:[@"AdaOSLab/" stringByAppendingString:version] forHTTPHeaderField:@"User-Agent"];
    if (body) {
        request.HTTPBody = [NSJSONSerialization dataWithJSONObject:body options:0 error:nil];
        if (!request.HTTPBody || request.HTTPBody.length > 184320) {
            [self finish:@"Session request body outside the limit. Nothing sent."]; return;
        }
        [request setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
    }
    if (token) [request setValue:[@"Bearer " stringByAppendingString:token] forHTTPHeaderField:@"Authorization"];
    self.task = [self.session dataTaskWithRequest:request];
    self.deadline = [NSTimer timerWithTimeInterval:operation == 5 ? 25 : 15 target:self selector:@selector(deadlineFired:) userInfo:nil repeats:NO];
    [[NSRunLoop mainRunLoop] addTimer:self.deadline forMode:NSRunLoopCommonModes];
    [self.task resume];
}

- (void)deadlineFired:(NSTimer *)timer {
    (void)timer;
    [self finish:@"Session request timed out. No automatic retry; a sent claim may have reached the server."];
}

- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task
    willPerformHTTPRedirection:(NSHTTPURLResponse *)response newRequest:(NSURLRequest *)request
    completionHandler:(void (^)(NSURLRequest *))completionHandler {
    (void)response; (void)request;
    completionHandler(nil);
    if (session == self.session && task == self.task) [self finish:@"Session redirect refused."];
}

- (void)URLSession:(NSURLSession *)session dataTask:(NSURLSessionDataTask *)task
    didReceiveResponse:(NSURLResponse *)response completionHandler:(void (^)(NSURLSessionResponseDisposition))completionHandler {
    if (session != self.session || task != self.task) { completionHandler(NSURLSessionResponseCancel); return; }
    if (![response isKindOfClass:[NSHTTPURLResponse class]]) {
        completionHandler(NSURLSessionResponseCancel); [self finish:@"Invalid session response."]; return;
    }
    NSInteger status = [(NSHTTPURLResponse *)response statusCode];
    if (status != 200 || ![response.MIMEType.lowercaseString isEqual:@"application/json"] ||
        response.expectedContentLength > (long long)BodyLimit) {
        completionHandler(NSURLSessionResponseCancel);
        [self finish:[NSString stringWithFormat:@"Session request refused (HTTP %ld), non-JSON or oversized response. No automatic retry.", (long)status]];
        return;
    }
    completionHandler(NSURLSessionResponseAllow);
}

- (void)URLSession:(NSURLSession *)session dataTask:(NSURLSessionDataTask *)task didReceiveData:(NSData *)data {
    if (session != self.session || task != self.task) return;
    if (data.length > BodyLimit - self.body.length) { [self finish:@"Session response exceeds 64 KiB. Check stopped."]; return; }
    [self.body appendData:data];
}

- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task didCompleteWithError:(NSError *)error {
    if (session != self.session || task != self.task) return;
    if (error) {
        [self finish:[NSString stringWithFormat:@"Session network error (code %ld). No automatic retry; a sent claim may have reached the server.", (long)error.code]];
        return;
    }
    id object = [NSJSONSerialization JSONObjectWithData:self.body options:0 error:nil];
    NSInteger operation = self.operation;
    [self clearNetwork];
    if (![object isKindOfClass:[NSDictionary class]] || !TrueBoolean(object[@"ok"])) {
        [self finish:@"Invalid session JSON. No credential saved."]; return;
    }
    if (operation == 1) {
        NSDictionary *preview = object[@"preview"];
        NSDictionary *scope = [preview isKindOfClass:[NSDictionary class]] ? preview[@"scope"] : nil;
        id expiry = [preview isKindOfClass:[NSDictionary class]] ? preview[@"expires_at"] : nil;
        double now = [[NSDate date] timeIntervalSince1970];
        if (![scope isKindOfClass:[NSDictionary class]] || ![scope[@"kind"] isEqual:@"webspace"] ||
            ![scope[@"id"] isEqual:self.target[@"webspace_id"]] ||
            ![preview[@"invite_id"] isEqual:self.target[@"invite_id"]] ||
            !TrueBoolean(preview[@"can_accept"]) || ![preview[@"status"] isEqual:@"pending"] ||
            ![preview[@"kind"] isEqual:@"guest_join_link"] || ![preview[@"max_sessions"] isEqual:@1] ||
            ![expiry isKindOfClass:[NSNumber class]] || !isfinite([expiry doubleValue]) ||
            [expiry doubleValue] <= now || [expiry doubleValue] > now + 1800) {
            [self finish:@"Invitation scope or lifetime rejected. No claim sent."]; return;
        }
        [self sendPath:[NSString stringWithFormat:@"/api/personalization/invites/%@/claim", self.target[@"invite_id"]]
                  body:@{@"expected_scope": @{@"kind": @"webspace", @"id": self.target[@"webspace_id"]},
                         @"device_name": @"AdaOS Lab legacy iPad"} token:nil operation:2];
    } else if (operation == 2) {
        NSDictionary *credential = object[@"desktop_credential"];
        if (![credential isKindOfClass:[NSDictionary class]] || ![credential[@"token_type"] isEqual:@"Bearer"] ||
            !credential[@"access_token"] || !credential[@"expires_at"] || !object[@"session_id"]) {
            [self finish:@"Invalid claimed session. No credential saved; review the claim on the PC."]; return;
        }
        NSDictionary *record = @{@"root_origin": self.target[@"root_origin"], @"hub_id": self.target[@"hub_id"],
            @"webspace_id": self.target[@"webspace_id"], @"session_id": object[@"session_id"],
            @"access_token": credential[@"access_token"], @"expires_at": credential[@"expires_at"]};
        double now = [[NSDate date] timeIntervalSince1970];
        if (!ValidSession(record) || [record[@"expires_at"] doubleValue] <= now || [record[@"expires_at"] doubleValue] > now + 1800) {
            [self finish:@"Claimed credential scope or lifetime rejected. Review the claim on the PC."]; return;
        }
        NSUInteger generation = self.generation;
        dispatch_async(self.storageQueue, ^{
            OSStatus status = WriteSession(record);
            if (status == errSecSuccess) [[NSFileManager defaultManager] removeItemAtPath:ProfilePath error:nil];
            dispatch_async(dispatch_get_main_queue(), ^{
                if (!self.running || generation != self.generation) return;
                [self finish:status == errSecSuccess ? @"Test session paired. Saved in Keychain. Tap Check session access." :
                    [NSString stringWithFormat:@"Session storage failed (code %ld). Review the claim on the PC.", (long)status]];
            });
        });
    } else if (operation == 3) {
        NSDictionary *runtime = object[@"runtime"];
        if (![runtime isKindOfClass:[NSDictionary class]] || ![runtime[@"selected_webspace_id"] isEqual:self.target[@"webspace_id"]]) {
            [self finish:@"Session response scope mismatch. Check stopped."]; return;
        }
        NSTimeInterval remaining = [self.target[@"expires_at"] doubleValue] - [[NSDate date] timeIntervalSince1970];
        [self finish:[NSString stringWithFormat:@"Session access OK.\nTest desktop only.\nKeychain session; expires in %.0f seconds.", MAX(0.0, remaining)]];
    } else if (operation == 4 || operation == 5) {
        if (![object[@"schema"] isEqual:@"adaos.desktop_voice.v1"] ||
            ![object[@"webspace_id"] isEqual:self.target[@"webspace_id"]] || !TrueBoolean(object[@"recognition_only"])) {
            [self finish:@"Voice response scope mismatch. Check stopped."]; return;
        }
        if (operation == 4) {
            if (!TrueBoolean(object[@"available"]) || ![object[@"provider"] isEqual:@"vosk"] ||
                ![object[@"sample_rate"] isEqual:@16000] || ![object[@"channels"] isEqual:@1] ||
                ![object[@"bits_per_sample"] isEqual:@16]) {
                [self finish:@"Voice recognition unavailable on the selected node. No audio recorded."]; return;
            }
            [self finish:@"Voice API OK. Scoped recognition is ready. No audio sent."];
        } else {
            id text = object[@"text"];
            if (![object[@"request_id"] isEqual:self.voiceRequestID] ||
                ![text isKindOfClass:[NSString class]] || [text length] > 4096) {
                [self finish:@"Invalid voice response. No automatic retry."]; return;
            }
            [self finish:[text length] ? [@"Transcription OK.\n" stringByAppendingString:text] :
                          @"Transcription complete. No speech recognized."];
        }
    } else {
        [self finish:@"Unexpected session operation."];
    }
}
@end
