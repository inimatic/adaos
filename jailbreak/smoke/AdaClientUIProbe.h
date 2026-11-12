#import <Foundation/Foundation.h>
@class AdaSubnetClient;
@interface AdaClientUIProbe : NSObject
+ (BOOL)prepare;
+ (NSDictionary *)report;
+ (BOOL)validJob:(id)job now:(NSTimeInterval)now;
- (void)observe:(AdaSubnetClient *)client;
- (void)cancel;
@end
