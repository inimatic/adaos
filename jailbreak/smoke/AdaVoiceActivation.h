#import <Foundation/Foundation.h>
@class AdaSubnetClient;
@interface AdaVoiceActivation : NSObject
@property(nonatomic, readonly) BOOL running;
@property(nonatomic, readonly) NSString *state;
@property(nonatomic, copy) void (^changed)(void);
@property(nonatomic, copy) void (^readReply)(NSString *);
+ (BOOL)prepareProbe;
+ (BOOL)prepareFixtureProbe;
+ (BOOL)prepareRecorderProbe;
+ (BOOL)prepareQueueProbe;
+ (NSData *)pcmFromWAV:(NSData *)wav;
+ (NSDictionary *)report;
+ (NSString *)targetFromDescriptor:(NSArray *)targets;
+ (BOOL)addressed:(NSString *)text;
- (void)observe:(AdaSubnetClient *)client;
- (BOOL)start:(AdaSubnetClient *)client;
- (void)stop:(NSString *)reason;
- (void)pauseForSpeech;
- (void)speechFinished;
@end
