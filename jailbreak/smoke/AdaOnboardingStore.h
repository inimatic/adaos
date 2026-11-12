#import <Foundation/Foundation.h>
#import "AdaOnboarding.h"

@interface AdaOnboardingStore : NSObject
@property(nonatomic) AdaOnboarding state;
- (BOOL)apply:(AdaOnboardingEvent)event;
- (void)save;
- (NSDictionary *)report;
@end
