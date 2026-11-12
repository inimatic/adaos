#import <Foundation/Foundation.h>
@interface AdaVoiceSetupStore : NSObject
@property(nonatomic) BOOL enabled;
@property(nonatomic, readonly) NSDictionary *report;
- (instancetype)initWithDefaults:(NSUserDefaults *)defaults;
- (void)begin;
- (void)observe:(NSDictionary *)activation speech:(NSDictionary *)speech;
- (void)confirmHeard:(BOOL)heard;
- (void)localReplayCompleted:(BOOL)completed;
- (void)finish;
@end
