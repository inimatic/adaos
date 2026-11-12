#import "AdaJSONRequest.h"

@interface AdaJSONRequest ()
@property(nonatomic, readwrite) BOOL running;
@property(nonatomic, strong) NSURLSession *session;
@property(nonatomic, strong) NSURLSessionDataTask *task;
@property(nonatomic, strong) NSMutableData *data;
@property(nonatomic, copy) AdaJSONCompletion completion;
@end
@implementation AdaJSONRequest
- (void)finish:(NSDictionary *)value error:(NSString *)error {
    if (!self.running) return;
    self.running = NO;
    self.task = nil;
    [self.session invalidateAndCancel]; self.session = nil; self.data = nil;
    AdaJSONCompletion completion = self.completion; self.completion = nil;
    if (completion) completion(value, error);
}
- (void)cancel { [self finish:nil error:@"cancelled"]; }
- (void)sendURL:(NSURL *)url method:(NSString *)method body:(NSDictionary *)body
    bearer:(NSString *)bearer completion:(AdaJSONCompletion)completion {
    NSAssert([NSThread isMainThread], @"Use the main queue");
    [self cancel];
    if (![url.scheme isEqual:@"https"] || ![@[@"api.inimatic.com", @"ru.api.inimatic.com"] containsObject:url.host] ||
        url.user || url.password || url.fragment || url.port) { completion(nil, @"invalid_target"); return; }
    NSData *data = body ? [NSJSONSerialization dataWithJSONObject:body options:0 error:nil] : nil;
    if ((body && !data) || data.length > 65536 || bearer.length > 8192) { completion(nil, @"request_too_large"); return; }
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:url cachePolicy:NSURLRequestReloadIgnoringLocalCacheData timeoutInterval:15];
    request.HTTPMethod = method; request.HTTPBody = data; request.HTTPShouldHandleCookies = NO;
    [request setValue:@"application/json" forHTTPHeaderField:@"Accept"];
    [request setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
    [request setValue:@"no-cache, no-store" forHTTPHeaderField:@"Cache-Control"];
    if (bearer.length) [request setValue:[@"Bearer " stringByAppendingString:bearer] forHTTPHeaderField:@"Authorization"];
    NSURLSessionConfiguration *configuration = [NSURLSessionConfiguration ephemeralSessionConfiguration];
    configuration.HTTPCookieStorage = nil; configuration.URLCache = nil; configuration.URLCredentialStorage = nil;
    configuration.timeoutIntervalForRequest = 15; configuration.timeoutIntervalForResource = 15;
    self.running = YES; self.data = [NSMutableData data]; self.completion = completion;
    self.session = [NSURLSession sessionWithConfiguration:configuration delegate:self delegateQueue:[NSOperationQueue mainQueue]];
    self.task = [self.session dataTaskWithRequest:request]; [self.task resume];
}
- (void)URLSession:(NSURLSession *)session dataTask:(NSURLSessionDataTask *)task didReceiveResponse:(NSURLResponse *)response
    completionHandler:(void (^)(NSURLSessionResponseDisposition))completionHandler {
    if (session != self.session || task != self.task) { completionHandler(NSURLSessionResponseCancel); return; }
    NSInteger status = [(NSHTTPURLResponse *)response statusCode];
    BOOL json = [response.MIMEType.lowercaseString isEqual:@"application/json"];
    if (status < 200 || status >= 300 || !json || response.expectedContentLength > 65536) {
        completionHandler(NSURLSessionResponseCancel);
        [self finish:nil error:status < 200 || status >= 300 ? [NSString stringWithFormat:@"HTTP_%ld", (long)status] : @"invalid_response"];
    } else completionHandler(NSURLSessionResponseAllow);
}
- (void)URLSession:(NSURLSession *)session dataTask:(NSURLSessionDataTask *)task didReceiveData:(NSData *)data {
    if (session != self.session || task != self.task) return;
    if (data.length > 65536 - self.data.length) [self finish:nil error:@"response_too_large"];
    else [self.data appendData:data];
}
- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task didCompleteWithError:(NSError *)error {
    if (session != self.session || task != self.task) return;
    if (error) { [self finish:nil error:[NSString stringWithFormat:@"network_%ld", (long)error.code]]; return; }
    id value = [NSJSONSerialization JSONObjectWithData:self.data options:0 error:nil];
    if (![value isKindOfClass:[NSDictionary class]]) [self finish:nil error:@"invalid_json"];
    else [self finish:value error:nil];
}
- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task willPerformHTTPRedirection:(NSHTTPURLResponse *)response
    newRequest:(NSURLRequest *)request completionHandler:(void (^)(NSURLRequest *))completionHandler {
    (void)response; (void)request;
    completionHandler(nil);
    if (session == self.session && task == self.task) [self finish:nil error:@"redirect_refused"];
}
@end
