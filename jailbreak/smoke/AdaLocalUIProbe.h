#import <Foundation/Foundation.h>
@interface AdaLocalUIProbe : NSObject
+ (BOOL)prepare;
+ (BOOL)consume;
+ (BOOL)validJob:(id)job now:(NSTimeInterval)now;
+ (void)saveKeyboardShown:(BOOL)shown dismissed:(BOOL)dismissed restored:(BOOL)restored;
+ (NSDictionary *)report;
@end
