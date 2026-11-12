#import <Foundation/Foundation.h>
typedef void (^AdaJSONCompletion)(NSDictionary *value, NSString *error);
@interface AdaJSONRequest : NSObject <NSURLSessionDataDelegate>
@property(nonatomic, readonly) BOOL running;
- (void)sendURL:(NSURL *)url method:(NSString *)method body:(NSDictionary *)body
    bearer:(NSString *)bearer completion:(AdaJSONCompletion)completion;
- (void)cancel;
@end
