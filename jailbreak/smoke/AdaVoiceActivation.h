#import <Foundation/Foundation.h>
@class AdaSubnetClient;
@interface AdaVoiceActivation : NSObject
@property(nonatomic, readonly) BOOL running;
@property(nonatomic, readonly) NSString *state;
@property(nonatomic, readonly) BOOL microphoneActive;
@property(nonatomic, readonly) BOOL continuous;
@property(nonatomic, readonly) NSDictionary *snapshot;
@property(nonatomic, copy) void (^changed)(void);
@property(nonatomic, copy) void (^readReply)(NSString *);
+ (BOOL)prepareProbe;
+ (BOOL)prepareLongProbe;
+ (BOOL)prepareFixtureProbe;
+ (BOOL)prepareRecorderProbe;
+ (BOOL)prepareQueueProbe;
+ (NSData *)pcmFromWAV:(NSData *)wav;
+ (NSDictionary *)report;
+ (NSString *)targetFromDescriptor:(NSArray *)targets;
+ (BOOL)addressed:(NSString *)text;
- (void)observe:(AdaSubnetClient *)client;
- (BOOL)start:(AdaSubnetClient *)client;
- (BOOL)start:(AdaSubnetClient *)client continuous:(BOOL)continuous;
- (void)stop:(NSString *)reason;
- (void)pauseForSpeech;
- (void)speechFinished;
@end
