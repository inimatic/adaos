#import <Foundation/Foundation.h>
#import "AdaWebIOTransport.h"
@interface AdaSubnetClient : NSObject
+ (NSDictionary *)chatEntryFromPayload:(NSDictionary *)payload session:(NSDictionary *)session;
@property(nonatomic, readonly) NSString *status;
@property(nonatomic, readonly) NSString *detail;
@property(nonatomic, readonly) NSURL *pairURL;
@property(nonatomic, readonly) NSDate *pairExpiry;
@property(nonatomic, readonly) NSArray *messages;
@property(nonatomic, readonly) BOOL ready;
@property(nonatomic, readonly) BOOL voicePending;
@property(nonatomic, readonly) BOOL waitingForReply;
@property(nonatomic, readonly) BOOL voiceReady;
@property(nonatomic, readonly) BOOL voiceChecked;
@property(nonatomic, readonly) NSArray *activationTargets;
@property(nonatomic, readonly) NSUInteger receivedFrames;
@property(nonatomic, readonly) NSUInteger receivedChatFrames;
@property(nonatomic, readonly) NSUInteger acceptedChatFrames;
@property(nonatomic, readonly) NSString *lastChatSender;
@property(nonatomic, readonly) AdaWebIOTransport *transport;
@property(nonatomic, copy) void (^changed)(void);
@property(nonatomic, copy) NSDictionary *diagnostics;
- (instancetype)initWithTransport:(AdaWebIOTransport *)transport;
- (void)start;
- (void)pairInZone:(NSString *)zone;
- (void)cancelPairing;
- (BOOL)sendText:(NSString *)text;
- (BOOL)sendVoice:(NSData *)wav russian:(BOOL)russian;
- (BOOL)sendActivatedText:(NSString *)text target:(NSString *)target;
- (BOOL)requestVoiceKind:(NSString *)kind payload:(NSDictionary *)payload completion:(void (^)(NSDictionary *, BOOL))completion;
- (void)cancelActivationRequests;
- (void)cancelVoiceInput;
- (void)suspend;
- (void)forget;
@end
