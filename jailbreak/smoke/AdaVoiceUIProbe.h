#import <Foundation/Foundation.h>
@interface AdaVoiceUIProbe : NSObject
+ (BOOL)prepare:(NSString *)action;
+ (NSString *)consumeForMicrophone:(BOOL)microphone speech:(BOOL)speech;
+ (void)save:(NSString *)action microphone:(BOOL)microphone speaking:(BOOL)speaking;
+ (NSDictionary *)report;
+ (void)presentation:(NSDictionary *)presentation;
@end
