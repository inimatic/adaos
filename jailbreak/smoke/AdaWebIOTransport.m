#import "AdaWebIOTransport.h"

@interface AdaScriptHandler : NSObject <WKScriptMessageHandler>
@property(nonatomic, weak) AdaWebIOTransport *owner;
@end

@interface AdaWebIOTransport () <WKNavigationDelegate>
@property(nonatomic, readwrite, strong) WKWebView *view;
@property(nonatomic, strong) NSURL *url;
@property(nonatomic, strong) WKNavigation *navigation;
@property(nonatomic, copy) NSString *connectionID;
@property(nonatomic) NSUInteger generation;
@end

@implementation AdaWebIOTransport
+ (NSDictionary *)report { return [[NSUserDefaults standardUserDefaults] dictionaryForKey:@"adaos.ios.transport.v1"] ?: @{}; }
- (void)saveState:(NSString *)state code:(NSInteger)code {
    [[NSUserDefaults standardUserDefaults] setObject:@{@"state": state, @"error_code": @(code), @"updated_at": @([NSDate date].timeIntervalSince1970)} forKey:@"adaos.ios.transport.v1"];
    [[NSUserDefaults standardUserDefaults] synchronize];
}
- (instancetype)init {
    self = [super init];
    if (!self) return nil;
    WKWebViewConfiguration *configuration = [[WKWebViewConfiguration alloc] init];
    configuration.websiteDataStore = [WKWebsiteDataStore nonPersistentDataStore];
    configuration.preferences.javaScriptCanOpenWindowsAutomatically = NO;
    AdaScriptHandler *handler = [[AdaScriptHandler alloc] init]; handler.owner = self;
    [configuration.userContentController addScriptMessageHandler:handler name:@"adaio"];
    self.view = [[WKWebView alloc] initWithFrame:CGRectMake(0, 0, 1, 1) configuration:configuration];
    self.view.navigationDelegate = self;
    self.view.userInteractionEnabled = NO;
    self.view.accessibilityElementsHidden = YES;
    return self;
}
- (NSString *)argument:(id)value {
    NSData *data = [NSJSONSerialization dataWithJSONObject:@[value] options:0 error:nil];
    NSString *json = data ? [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] : nil;
    json = [json stringByReplacingOccurrencesOfString:@"\u2028" withString:@"\\u2028"];
    json = [json stringByReplacingOccurrencesOfString:@"\u2029" withString:@"\\u2029"];
    return json ? [NSString stringWithFormat:@"(%@)[0]", json] : nil;
}
- (void)connectURL:(NSURL *)url {
    [self stop];
    if (![@[@"api.inimatic.com", @"ru.api.inimatic.com"] containsObject:url.host] ||
        ![url.scheme isEqual:@"wss"] || url.user || url.password || url.fragment || url.port) {
        if (self.event) self.event(@"invalid-target", nil); return;
    }
    self.url = url;
    [self saveState:@"loading-local-adapter" code:0];
    self.connectionID = [NSUUID UUID].UUIDString;
    NSURL *scriptURL = [[NSBundle mainBundle] URLForResource:@"webio-transport" withExtension:@"js"];
    NSString *source = scriptURL ? [NSString stringWithContentsOfURL:scriptURL encoding:NSUTF8StringEncoding error:nil] : nil;
    if (!source.length || source.length > 16384) { if (self.event) self.event(@"unavailable", nil); return; }
    WKUserScript *script = [[WKUserScript alloc] initWithSource:source injectionTime:WKUserScriptInjectionTimeAtDocumentEnd forMainFrameOnly:YES];
    [self.view.configuration.userContentController removeAllUserScripts];
    [self.view.configuration.userContentController addUserScript:script];
    self.navigation = [self.view loadHTMLString:@"<!doctype html><meta charset='utf-8'><title>AdaOS transport</title>" baseURL:nil];
}
- (void)webView:(WKWebView *)webView didFinishNavigation:(WKNavigation *)navigation {
    if (webView != self.view || !self.url || navigation != self.navigation) return;
    [self saveState:@"local-adapter-loaded" code:0];
    NSString *argument = [self argument:self.url.absoluteString];
    if (!argument) return;
    NSUInteger generation = self.generation;
    [webView evaluateJavaScript:[NSString stringWithFormat:@"window.AdaTransport.connect(%@,%@)", argument, [self argument:self.connectionID]] completionHandler:^(id value, NSError *error) {
        if (generation != self.generation) return;
        [self saveState:(!error && [value boolValue]) ? @"socket-requested" : @"adapter-error" code:error.code];
        if ((error || ![value boolValue]) && self.event) self.event(@"error", nil);
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 3*NSEC_PER_SEC), dispatch_get_main_queue(), ^{
            if (generation != self.generation || !self.url) return;
            [webView evaluateJavaScript:@"window.AdaTransport.status()" completionHandler:^(id state, NSError *statusError) {
                if (generation == self.generation && !statusError && [state isKindOfClass:[NSNumber class]])
                    [self saveState:@"socket-observed" code:[state integerValue]];
            }];
        });
    }];
}
- (void)webView:(WKWebView *)webView decidePolicyForNavigationAction:(WKNavigationAction *)action
    decisionHandler:(void (^)(WKNavigationActionPolicy))decisionHandler {
    (void)webView;
    BOOL local = action.targetFrame.mainFrame && [action.request.URL.scheme isEqual:@"about"];
    decisionHandler(local ? WKNavigationActionPolicyAllow : WKNavigationActionPolicyCancel);
}
- (void)webView:(WKWebView *)webView didFailProvisionalNavigation:(WKNavigation *)navigation withError:(NSError *)error {
    (void)webView; (void)navigation;
    [self saveState:@"navigation-error" code:error.code];
    if (self.url && self.event) self.event(@"error", nil);
}
- (void)send:(NSDictionary *)envelope completion:(void (^)(BOOL))completion {
    NSString *argument = [self argument:envelope];
    if (!self.url || !argument) { if (completion) completion(NO); return; }
    NSUInteger generation = self.generation;
    [self.view evaluateJavaScript:[NSString stringWithFormat:@"window.AdaTransport.send(%@)", argument]
        completionHandler:^(id value, NSError *error) {
            if (generation == self.generation && completion) completion(!error && [value boolValue]);
        }];
}
- (void)stop {
    NSString *retiringID = self.connectionID;
    self.generation++; self.url = nil; self.connectionID = nil; self.navigation = nil;
    [self.view stopLoading];
    if (retiringID) [self.view evaluateJavaScript:[NSString stringWithFormat:@"if(window.AdaTransport){window.AdaTransport.stop(%@);}", [self argument:retiringID]] completionHandler:nil];
}
- (void)dealloc {
    [self.view.configuration.userContentController removeScriptMessageHandlerForName:@"adaio"];
    [self.view stopLoading];
}
@end

@implementation AdaScriptHandler
- (void)userContentController:(WKUserContentController *)controller didReceiveScriptMessage:(WKScriptMessage *)message {
    (void)controller;
    AdaWebIOTransport *owner = self.owner;
    if (!owner.url || !message.frameInfo.mainFrame || message.webView != owner.view ||
        ![message.body isKindOfClass:[NSDictionary class]]) return;
    if (![owner.connectionID isEqual:message.body[@"connection"]]) return;
    NSString *kind = message.body[@"kind"];
    if (![kind isKindOfClass:[NSString class]]) return;
    if ([kind isEqual:@"message"]) {
        NSString *text = message.body[@"data"];
        if (![text isKindOfClass:[NSString class]] || text.length > 196608) return;
        NSData *data = [text dataUsingEncoding:NSUTF8StringEncoding];
        if (data.length > 262144) return;
        id value = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
        if ([value isKindOfClass:[NSDictionary class]] && owner.event) owner.event(kind, value);
    } else if ([@[@"open", @"closed", @"error", @"oversized", @"invalid-target"] containsObject:kind] && owner.event) owner.event(kind, nil);
}
@end
