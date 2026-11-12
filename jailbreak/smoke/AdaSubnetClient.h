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
@property(nonatomic, readonly) BOOL voiceReady;
@property(nonatomic, readonly) BOOL voiceChecked;
@property(nonatomic, readonly) AdaWebIOTransport *transport;
@property(nonatomic, copy) void (^changed)(void);
@property(nonatomic, copy) NSDictionary *diagnostics;
- (void)start;
- (void)pairInZone:(NSString *)zone;
- (void)cancelPairing;
- (BOOL)sendText:(NSString *)text;
- (BOOL)sendVoice:(NSData *)wav russian:(BOOL)russian;
- (void)suspend;
- (void)forget;
@end
