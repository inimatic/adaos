#import <Foundation/Foundation.h>
@class AdaSubnetClient;
@interface AdaClientUIProbe : NSObject
@property(nonatomic, copy) void (^readReply)(void);
+ (BOOL)prepare;
+ (BOOL)prepareSpeechCheck;
+ (NSDictionary *)report;
+ (BOOL)validJob:(id)job now:(NSTimeInterval)now;
- (void)observe:(AdaSubnetClient *)client;
- (void)cancel;
@end
