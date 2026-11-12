#import <UIKit/UIKit.h>
#import <WebKit/WebKit.h>

@interface AdaWebIOTransport : NSObject
+ (NSDictionary *)report;
@property(nonatomic, readonly) WKWebView *view;
@property(nonatomic, copy) void (^event)(NSString *kind, NSDictionary *message);
- (void)connectURL:(NSURL *)url;
- (void)send:(NSDictionary *)envelope completion:(void (^)(BOOL sent))completion;
- (void)stop;
@end
