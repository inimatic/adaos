#import <Foundation/Foundation.h>

typedef void (^LabProbeCompletion)(NSString *status);

@interface LabHTTPSProbe : NSObject <NSURLSessionDataDelegate>
@property(nonatomic, readonly, getter=isRunning) BOOL running;
- (void)startWithCompletion:(LabProbeCompletion)completion;
- (void)startRootPingWithCompletion:(LabProbeCompletion)completion;
- (void)cancel;
@end
