#import "AdaVoiceSegmentBuffer.h"
#import <math.h>
@interface AdaVoiceSegmentBuffer ()
@property(nonatomic, strong) NSMutableArray *items;
@property(nonatomic, readwrite) NSUInteger dropped;
@end
@implementation AdaVoiceSegmentBuffer
- (instancetype)init { self = [super init]; if (self) self.items = [NSMutableArray array]; return self; }
- (NSUInteger)count { return self.items.count; }
- (void)expire:(double)now {
    while (self.items.count && (!isfinite(now) || now >= [self.items[0][@"expires"] doubleValue])) {
        [self.items removeObjectAtIndex:0]; self.dropped++;
    }
}
- (BOOL)append:(NSData *)pcm followup:(BOOL)followup now:(double)now {
    [self expire:now];
    if (!isfinite(now) || !pcm.length || pcm.length % 2 || pcm.length >= 384000 || self.items.count >= 2) {
        self.dropped++; return NO;
    }
    [self.items addObject:@{@"pcm": [pcm copy], @"followup": @(followup), @"expires": @(now + 6)}];
    return YES;
}
- (NSDictionary *)take:(double)now {
    [self expire:now];
    if (!self.items.count) return nil;
    NSDictionary *item = self.items[0]; [self.items removeObjectAtIndex:0]; return item;
}
- (void)discard { self.dropped += self.items.count; [self.items removeAllObjects]; }
- (void)reset { [self.items removeAllObjects]; self.dropped = 0; }
@end
