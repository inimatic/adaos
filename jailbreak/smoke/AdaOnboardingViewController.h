#import <UIKit/UIKit.h>

@interface AdaOnboardingViewController : UIViewController
@property(nonatomic, copy) UIViewController *(^labFactory)(void);
@property(nonatomic, copy) UIViewController *(^connectionFactory)(void);
@end
