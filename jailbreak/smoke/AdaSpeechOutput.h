#import <Foundation/Foundation.h>
@interface AdaSpeechOutput : NSObject
@property(nonatomic, readonly) BOOL busy;
@property(nonatomic, readonly) NSString *state;
@property(nonatomic, copy) void (^changed)(void);
+ (NSDictionary *)report;
- (void)speak:(NSString *)text language:(NSString *)language;
- (void)cancel;
@end
