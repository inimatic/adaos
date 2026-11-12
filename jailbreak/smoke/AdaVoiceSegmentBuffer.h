#import <Foundation/Foundation.h>

/* A short, in-memory recognition backlog, never a dialog-command queue. */
@interface AdaVoiceSegmentBuffer : NSObject
@property(nonatomic, readonly) NSUInteger count;
@property(nonatomic, readonly) NSUInteger dropped;
- (BOOL)append:(NSData *)pcm followup:(BOOL)followup now:(double)now;
- (NSDictionary *)take:(double)now;
- (void)discard;
- (void)reset;
@end
