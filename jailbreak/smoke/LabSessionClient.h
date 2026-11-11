#import <Foundation/Foundation.h>

typedef void (^LabSessionCompletion)(NSString *status);

@interface LabSessionClient : NSObject <NSURLSessionDataDelegate>
@property(nonatomic, readonly, getter=isRunning) BOOL running;
- (void)pairWithCompletion:(LabSessionCompletion)completion;
- (void)checkWithCompletion:(LabSessionCompletion)completion;
- (void)checkVoiceWithCompletion:(LabSessionCompletion)completion;
- (void)transcribeWAV:(NSData *)wav completion:(LabSessionCompletion)completion;
- (void)forgetWithCompletion:(LabSessionCompletion)completion;
- (void)cancel;
@end
