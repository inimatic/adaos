#import <Foundation/Foundation.h>
#import <AVFoundation/AVFoundation.h>

typedef void (^LabAudioCompletion)(NSString *status);
typedef void (^LabCapturedAudioCompletion)(NSData *wav, NSString *status);

@interface LabAudioProbe : NSObject <AVAudioRecorderDelegate, AVAudioPlayerDelegate>
@property(nonatomic, readonly, getter=isRunning) BOOL running;
+ (NSString *)permission;
+ (void)requestPermissionWithCompletion:(LabAudioCompletion)completion;
+ (NSData *)oneSecondSilenceWAV;
- (void)recordShortSampleWithCompletion:(LabAudioCompletion)completion;
- (void)recordShortSampleAndReplayWithCompletion:(LabAudioCompletion)completion;
- (void)playSyntheticToneWithCompletion:(LabAudioCompletion)completion;
- (void)captureWAVWithCompletion:(LabCapturedAudioCompletion)completion;
- (void)cancel;
@end
