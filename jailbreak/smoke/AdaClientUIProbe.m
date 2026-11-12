#import "AdaClientUIProbe.h"
#import "AdaAppStorage.h"
#import "AdaSubnetClient.h"
#import "AdaSubnetSession.h"
#import <fcntl.h>
#import <unistd.h>
#import <math.h>

static NSString *const ReportKey = @"adaos.ios.client-probe.v1";
static NSString *JobPath(void) { return [AdaAppStorageDirectory() stringByAppendingPathComponent:@"client-probe.json"]; }
@interface AdaClientUIProbe ()
@property(nonatomic) BOOL active;
@property(nonatomic) BOOL registered;
@property(nonatomic) BOOL attempted;
@property(nonatomic) BOOL accepted;
@property(nonatomic) BOOL reply;
@property(nonatomic) BOOL assistantReceived;
@property(nonatomic) BOOL speechCheck;
@property(nonatomic) BOOL replyReadRequested;
@property(nonatomic, weak) AdaSubnetClient *client;
@property(nonatomic, strong) NSSet *baselineMessageIDs;
@end
@implementation AdaClientUIProbe
+ (BOOL)validJob:(id)job now:(NSTimeInterval)now {
    if (![job isKindOfClass:[NSDictionary class]] || [job count] != 2 ||
        ![@[@"conversational-text-check", @"conversational-reply-speech-check"] containsObject:job[@"operation"]] ||
        ![job[@"expires_at"] isKindOfClass:[NSNumber class]]) return NO;
    double expiry = [job[@"expires_at"] doubleValue];
    return isfinite(expiry) && expiry > now && expiry <= now + 120;
}
+ (BOOL)prepare {
    return [self prepareOperation:@"conversational-text-check"];
}
+ (BOOL)prepareSpeechCheck {
    return [self prepareOperation:@"conversational-reply-speech-check"];
}
+ (BOOL)prepareOperation:(NSString *)operation {
    NSInteger status;
    if (getuid() != 501 || ![AdaSubnetSession read:&status] || !AdaEnsurePrivateStorageDirectory()) return NO;
    NSData *data = [NSJSONSerialization dataWithJSONObject:@{@"operation": operation,
        @"expires_at": @([NSDate date].timeIntervalSince1970 + 90)} options:0 error:nil];
    if (!data) return NO;
    int fd = open(JobPath().fileSystemRepresentation, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
    if (fd < 0) return NO;
    BOOL ok = write(fd, data.bytes, data.length) == (ssize_t)data.length;
    close(fd);
    if (!ok) unlink(JobPath().fileSystemRepresentation);
    if (ok) {
        [[NSUserDefaults standardUserDefaults] setObject:@{@"state": @"prepared"} forKey:ReportKey];
        [[NSUserDefaults standardUserDefaults] synchronize];
    }
    return ok;
}
+ (NSDictionary *)report {
    id saved = [[NSUserDefaults standardUserDefaults] objectForKey:ReportKey];
    if (![saved isKindOfClass:[NSDictionary class]]) return @{@"state": @"absent"};
    NSMutableDictionary *safe = [NSMutableDictionary dictionary];
    if ([@[@"prepared", @"running", @"passed", @"timeout", @"failed", @"cancelled", @"expired"] containsObject:saved[@"state"]])
        safe[@"state"] = saved[@"state"];
    for (NSString *key in @[@"registered", @"test_send_attempted", @"accepted_user_message", @"assistant_message_received", @"own_endpoint_reply", @"reply_read_requested"])
        if ([saved[key] isKindOfClass:[NSNumber class]]) safe[key] = @([saved[key] boolValue]);
    if ([@[@"disconnected", @"connecting", @"connected", @"offline", @"suspended", @"authorization_required", @"storage_error"] containsObject:saved[@"connection_state"]])
        safe[@"connection_state"] = saved[@"connection_state"];
    for (NSString *key in @[@"received_frames", @"received_chat_frames", @"accepted_chat_frames"])
        if ([saved[key] isKindOfClass:[NSNumber class]]) safe[key] = @([saved[key] unsignedIntegerValue]);
    if ([@[@"user", @"assistant", @"hub", @"other", @"none"] containsObject:saved[@"last_chat_sender"]]) safe[@"last_chat_sender"] = saved[@"last_chat_sender"];
    return safe.count ? safe : @{@"state": @"absent"};
}
- (instancetype)init {
    self = [super init]; if (!self || !AdaEnsurePrivateStorageDirectory()) return self;
    NSFileManager *manager = [NSFileManager defaultManager];
    NSDictionary *attr = [manager attributesOfItemAtPath:JobPath() error:nil];
    if (![attr[NSFileType] isEqual:NSFileTypeRegular] || [attr[NSFileSize] unsignedLongLongValue] > 512 ||
        [attr[NSFileOwnerAccountID] unsignedIntegerValue] != getuid() || ([attr[NSFilePosixPermissions] unsignedIntegerValue] & 0077)) return self;
    NSData *data = [NSData dataWithContentsOfFile:JobPath()];
    id job = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
    if (![manager removeItemAtPath:JobPath() error:nil]) return self; /* Consume before any request, including expired jobs. */
    self.active = [AdaClientUIProbe validJob:job now:[NSDate date].timeIntervalSince1970];
    self.speechCheck = self.active && [job[@"operation"] isEqual:@"conversational-reply-speech-check"];
    [self save:self.active ? @"running" : @"expired"];
    if (self.active) {
        __weak AdaClientUIProbe *weakSelf = self;
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 40 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{
            if (weakSelf.active) [weakSelf finish:@"timeout"];
        });
    }
    return self;
}
- (void)save:(NSString *)state {
    [[NSUserDefaults standardUserDefaults] setObject:@{@"state": state, @"registered": @(self.registered),
        @"test_send_attempted": @(self.attempted), @"accepted_user_message": @(self.accepted),
        @"assistant_message_received": @(self.assistantReceived), @"own_endpoint_reply": @(self.reply), @"reply_read_requested": @(self.replyReadRequested), @"connection_state": self.client.status ?: @"disconnected",
        @"received_frames": @(self.client.receivedFrames), @"received_chat_frames": @(self.client.receivedChatFrames),
        @"accepted_chat_frames": @(self.client.acceptedChatFrames), @"last_chat_sender": self.client.lastChatSender ?: @"none"} forKey:ReportKey];
    [[NSUserDefaults standardUserDefaults] synchronize];
}
- (void)finish:(NSString *)state { self.active = NO; [self save:state]; }
- (void)observe:(AdaSubnetClient *)client {
    if (!self.active) return;
    self.client = client;
    if (client.ready) {
        self.registered = YES;
        if (!self.attempted) {
            self.baselineMessageIDs = [NSSet setWithArray:[client.messages valueForKey:@"id"]];
            self.attempted = YES; /* sendText synchronously triggers this observer again. */
            if (![client sendText:@"Connection test from iPad. Please reply with OK."]) { [self finish:@"failed"]; return; }
        }
    }
    for (NSDictionary *entry in client.messages) {
        if (!self.attempted || [self.baselineMessageIDs containsObject:entry[@"id"]]) continue;
        if ([entry[@"from"] isEqual:@"user"] && [entry[@"delivery"] isEqual:@"sent"]) self.accepted = YES;
        if ([entry[@"from"] isEqual:@"assistant"]) self.assistantReceived = YES;
        if ([entry[@"from"] isEqual:@"assistant"] && [entry[@"automatic_speech"] boolValue]) self.reply = YES;
    }
    if (self.accepted && self.assistantReceived) {
        self.replyReadRequested = self.speechCheck && self.reply && self.readReply != nil;
        [self finish:@"passed"];
        if (self.replyReadRequested) self.readReply();
    }
    else if ([@[@"offline", @"authorization_required", @"storage_error"] containsObject:client.status]) [self finish:@"failed"];
    else [self save:@"running"];
}
- (void)cancel { if (self.active) [self finish:@"cancelled"]; }
@end
