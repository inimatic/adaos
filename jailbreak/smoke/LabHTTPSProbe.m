#import "LabHTTPSProbe.h"
#import <math.h>

@interface LabHTTPSProbe ()
@property(nonatomic, readwrite, getter=isRunning) BOOL running;
@property(nonatomic, strong) NSURLSession *session;
@property(nonatomic, strong) NSURLSessionDataTask *task;
@property(nonatomic, strong) NSTimer *deadline;
@property(nonatomic, strong) NSDate *started;
@property(nonatomic, strong) NSHTTPURLResponse *response;
@property(nonatomic, copy) LabProbeCompletion completion;
@property(nonatomic) BOOL rootPing;
@property(nonatomic, strong) NSMutableData *body;
@end

@implementation LabHTTPSProbe
- (void)startWithCompletion:(LabProbeCompletion)completion {
    [self startRootPing:NO completion:completion];
}

- (void)startRootPingWithCompletion:(LabProbeCompletion)completion {
    [self startRootPing:YES completion:completion];
}

- (void)startRootPing:(BOOL)rootPing completion:(LabProbeCompletion)completion {
    NSAssert([NSThread isMainThread], @"The probe runs on the main queue");
    if (self.running) {
        [self cancel];
    }
    self.running = YES;
    self.completion = completion;
    self.started = [NSDate date];
    self.rootPing = rootPing;
    self.body = rootPing ? [NSMutableData data] : nil;

    NSURLSessionConfiguration *configuration = [NSURLSessionConfiguration ephemeralSessionConfiguration];
    configuration.URLCache = nil;
    configuration.HTTPCookieStorage = nil;
    configuration.URLCredentialStorage = nil;
    configuration.HTTPShouldSetCookies = NO;
    configuration.allowsCellularAccess = NO;
    configuration.timeoutIntervalForRequest = 10.0;
    configuration.timeoutIntervalForResource = 15.0;
    configuration.HTTPMaximumConnectionsPerHost = 1;
    // Keep the platform's certificate verification and ATS defaults intact.
    self.session = [NSURLSession sessionWithConfiguration:configuration delegate:self
                                          delegateQueue:[NSOperationQueue mainQueue]];
    NSString *target = rootPing ? @"https://api.inimatic.com/api/ping" : @"https://inimatic.com/";
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:target]
                                                         cachePolicy:NSURLRequestReloadIgnoringLocalCacheData
                                                     timeoutInterval:10.0];
    request.HTTPMethod = rootPing ? @"GET" : @"HEAD";
    request.HTTPShouldHandleCookies = NO;
    [request setValue:rootPing ? @"application/json" : @"text/html" forHTTPHeaderField:@"Accept"];
    NSString *version = [[NSBundle mainBundle] objectForInfoDictionaryKey:@"CFBundleShortVersionString"];
    [request setValue:[@"AdaOSLab/" stringByAppendingString:version] forHTTPHeaderField:@"User-Agent"];
    self.task = [self.session dataTaskWithRequest:request];
    self.deadline = [NSTimer timerWithTimeInterval:15.0 target:self
                                         selector:@selector(deadlineFired:) userInfo:nil repeats:NO];
    [[NSRunLoop mainRunLoop] addTimer:self.deadline forMode:NSRunLoopCommonModes];
    [self.task resume];
}

- (void)finish:(NSString *)status {
    if (!self.running) {
        return;
    }
    self.running = NO;
    [self.deadline invalidate];
    self.deadline = nil;
    LabProbeCompletion completion = self.completion;
    NSURLSession *session = self.session;
    self.completion = nil;
    self.session = nil;
    self.task = nil;
    self.response = nil;
    self.started = nil;
    self.body = nil;
    [session invalidateAndCancel];
    if (completion) {
        completion(status);
    }
}

- (void)cancel {
    [self finish:@"Check cancelled. Tap to try again."];
}

- (void)deadlineFired:(NSTimer *)timer {
    (void)timer;
    [self finish:@"Network timeout. Tap to try again."];
}

- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task
        willPerformHTTPRedirection:(NSHTTPURLResponse *)response
        newRequest:(NSURLRequest *)request
        completionHandler:(void (^)(NSURLRequest *))completionHandler {
    (void)request;
    if (session == self.session && task == self.task) {
        self.response = response;
    }
    // Each check contacts its fixed reviewed HTTPS target; never follow redirects.
    completionHandler(nil);
}

- (void)URLSession:(NSURLSession *)session dataTask:(NSURLSessionDataTask *)dataTask
        didReceiveResponse:(NSURLResponse *)response
        completionHandler:(void (^)(NSURLSessionResponseDisposition))completionHandler {
    if (session != self.session || dataTask != self.task) {
        completionHandler(NSURLSessionResponseCancel);
        return;
    }
    if (![response isKindOfClass:[NSHTTPURLResponse class]]) {
        completionHandler(NSURLSessionResponseCancel);
        [self finish:@"The server did not return an HTTP response."];
        return;
    }
    self.response = (NSHTTPURLResponse *)response;
    if (self.rootPing) {
        if (self.response.statusCode != 200) {
            completionHandler(NSURLSessionResponseCancel);
            [self finish:[NSString stringWithFormat:@"Root API returned HTTP %ld. No redirect or authorization fallback.",
                         (long)self.response.statusCode]];
            return;
        }
        if (![self.response.MIMEType.lowercaseString isEqualToString:@"application/json"]) {
            completionHandler(NSURLSessionResponseCancel);
            [self finish:@"Root API returned a non-JSON response. Check stopped."];
            return;
        }
        if (response.expectedContentLength > 4096) {
            completionHandler(NSURLSessionResponseCancel);
            [self finish:@"Root API response exceeds 4 KiB. Check stopped."];
            return;
        }
    }
    completionHandler(NSURLSessionResponseAllow);
}

- (void)URLSession:(NSURLSession *)session dataTask:(NSURLSessionDataTask *)dataTask
        didReceiveData:(NSData *)data {
    if (session == self.session && dataTask == self.task && data.length > 0) {
        if (self.rootPing) {
            // Enforce the limit on decoded bytes too, including chunked/gzip responses.
            if (data.length > 4096 - self.body.length) {
                [self finish:@"Root API response exceeds 4 KiB. Check stopped."];
                return;
            }
            [self.body appendData:data];
            return;
        }
        // A HEAD probe needs no body; do not buffer or persist server content.
        [self finish:@"Unexpected response body. Check stopped."];
    }
}

- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task
        didCompleteWithError:(NSError *)error {
    if (session != self.session || task != self.task) {
        return;
    }
    if (error) {
        NSString *message = @"Network check failed";
        if ([error.domain isEqualToString:NSURLErrorDomain]) {
            switch (error.code) {
                case NSURLErrorNotConnectedToInternet:
                case NSURLErrorNetworkConnectionLost:
                case NSURLErrorDataNotAllowed:
                    message = @"Wi-Fi is unavailable";
                    break;
                case NSURLErrorTimedOut:
                    message = @"Network timeout";
                    break;
                case NSURLErrorSecureConnectionFailed:
                case NSURLErrorServerCertificateHasBadDate:
                case NSURLErrorServerCertificateUntrusted:
                case NSURLErrorServerCertificateHasUnknownRoot:
                case NSURLErrorServerCertificateNotYetValid:
                case NSURLErrorAppTransportSecurityRequiresSecureConnection:
                    message = @"HTTPS validation failed";
                    break;
                case NSURLErrorCannotFindHost:
                case NSURLErrorDNSLookupFailed:
                case NSURLErrorCannotConnectToHost:
                    message = @"Server is unreachable";
                    break;
                default:
                    break;
            }
        }
        [self finish:[NSString stringWithFormat:@"%@ (code %ld). Tap to try again.", message, (long)error.code]];
        return;
    }
    if (!self.response) {
        [self finish:@"The server did not return an HTTP response."];
        return;
    }
    NSInteger code = self.response.statusCode;
    NSTimeInterval elapsed = [[NSDate date] timeIntervalSinceDate:self.started];
    if (self.rootPing) {
        NSError *parseError = nil;
        id payload = [NSJSONSerialization JSONObjectWithData:self.body options:0 error:&parseError];
        if (parseError || ![payload isKindOfClass:[NSDictionary class]]) {
            [self finish:@"Root API returned invalid JSON. Check stopped."];
            return;
        }
        NSDictionary *object = (NSDictionary *)payload;
        id ok = object[@"ok"];
        id timestamp = object[@"ts"];
        BOOL validOK = [ok isKindOfClass:[NSNumber class]] &&
            CFGetTypeID((__bridge CFTypeRef)ok) == CFBooleanGetTypeID() && [ok boolValue];
        BOOL validTimestamp = [timestamp isKindOfClass:[NSNumber class]] &&
            CFGetTypeID((__bridge CFTypeRef)timestamp) != CFBooleanGetTypeID() &&
            isfinite([timestamp doubleValue]) && [timestamp doubleValue] > 0 &&
            [timestamp doubleValue] < 253402300800000.0;
        if (!validOK || ![object[@"service"] isEqual:@"root"] || !validTimestamp) {
            [self finish:@"Root API JSON does not match the ping contract. Check stopped."];
            return;
        }
        NSDateFormatter *formatter = [[NSDateFormatter alloc] init];
        formatter.dateStyle = NSDateFormatterShortStyle;
        formatter.timeStyle = NSDateFormatterMediumStyle;
        NSString *serverTime = [formatter stringFromDate:[NSDate dateWithTimeIntervalSince1970:[timestamp doubleValue] / 1000.0]];
        [self finish:[NSString stringWithFormat:@"Root API OK: HTTP 200, JSON, %.1f s.\nservice: root\nServer time: %@\nUser/node session is not connected yet.", elapsed, serverTime]];
        return;
    }
    if (code >= 300 && code < 400) {
        [self finish:[NSString stringWithFormat:@"HTTPS answered: HTTP %ld. Redirect not followed.", (long)code]];
    } else if (code >= 200 && code < 300) {
        NSString *type = self.response.MIMEType ?: @"unknown type";
        type = [type substringToIndex:MIN(type.length, (NSUInteger)64)];
        [self finish:[NSString stringWithFormat:@"HTTPS OK: HTTP %ld, %@, %.1f s.\nWebsite reached. AdaOS API access is not tested yet.",
                     (long)code, type, elapsed]];
    } else {
        [self finish:[NSString stringWithFormat:@"HTTPS answered: HTTP %ld. AdaOS API access is not tested yet.", (long)code]];
    }
}
@end
