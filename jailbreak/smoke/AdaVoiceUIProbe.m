#import "AdaVoiceUIProbe.h"
#import "AdaAppStorage.h"
#import <fcntl.h>
#import <unistd.h>
#import <math.h>
static NSString *Job(void) { return [AdaAppStorageDirectory() stringByAppendingPathComponent:@"voice-ui-probe.json"]; }
static BOOL Allowed(NSString *action) { return [@[@"stop-microphone", @"stop-speech", @"disconnect", @"open-setup", @"start-setup", @"finish-setup", @"local-microphone", @"reconnect"] containsObject:action]; }
@implementation AdaVoiceUIProbe
+ (BOOL)prepare:(NSString *)action {
    if (getuid() != 501 || !Allowed(action) || !AdaEnsurePrivateStorageDirectory()) return NO;
    NSData *data = [NSJSONSerialization dataWithJSONObject:@{@"action": action, @"expires_at": @([NSDate date].timeIntervalSince1970+60)} options:0 error:nil];
    int fd = open(Job().fileSystemRepresentation, O_CREAT | O_EXCL | O_WRONLY | O_NOFOLLOW, 0600);
    if (fd < 0) return NO;
    BOOL ok = write(fd,data.bytes,data.length) == (ssize_t)data.length; close(fd);
    if (!ok) unlink(Job().fileSystemRepresentation);
    return ok;
}
+ (NSString *)consumeForMicrophone:(BOOL)microphone speech:(BOOL)speech {
    NSDictionary *a = [[NSFileManager defaultManager] attributesOfItemAtPath:Job() error:nil];
    if (![a[NSFileType] isEqual:NSFileTypeRegular] || [a[NSFileOwnerAccountID] unsignedIntegerValue] != getuid() ||
        ([a[NSFilePosixPermissions] unsignedIntegerValue]&0077) || [a[NSFileSize] unsignedLongLongValue]>512) return nil;
    NSData *data = [NSData dataWithContentsOfFile:Job()];
    id value = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
    if (![value isKindOfClass:[NSDictionary class]] || [value count]!=2 || !Allowed(value[@"action"]) ||
        ![value[@"expires_at"] isKindOfClass:[NSNumber class]]) { [[NSFileManager defaultManager] removeItemAtPath:Job() error:nil]; return nil; }
    double expiry = [value[@"expires_at"] doubleValue], now = [NSDate date].timeIntervalSince1970;
    BOOL valid = isfinite(expiry) && expiry>now && expiry<=now+60;
    if (valid && (([value[@"action"] isEqual:@"stop-speech"] && !speech) || ([value[@"action"] isEqual:@"stop-microphone"] && !microphone))) return nil;
    if (![[NSFileManager defaultManager] removeItemAtPath:Job() error:nil]) return nil;
    return valid ? value[@"action"] : nil;
}
+ (void)save:(NSString *)action microphone:(BOOL)microphone speaking:(BOOL)speaking {
    NSMutableDictionary *report = [[self report] mutableCopy];
    [report addEntriesFromDictionary:@{@"action": action, @"action_microphone_active": @(microphone), @"action_speaking": @(speaking),
        @"updated_at": @([NSDate date].timeIntervalSince1970)}];
    [[NSUserDefaults standardUserDefaults] setObject:report forKey:@"adaos.ios.voice-ui-probe.v1"];
    [[NSUserDefaults standardUserDefaults] synchronize];
}
+ (void)presentation:(NSDictionary *)presentation {
    NSMutableDictionary *report = [[self report] mutableCopy];
    if ([report[@"presentation"] isEqual:presentation]) return;
    report[@"presentation"] = presentation;
    report[@"presentation_updated_at"] = @([NSDate date].timeIntervalSince1970);
    NSMutableDictionary *seen = [report[@"states_seen"] mutableCopy] ?: [NSMutableDictionary dictionary];
    seen[presentation[@"label"]] = report[@"presentation_updated_at"]; report[@"states_seen"] = seen;
    [[NSUserDefaults standardUserDefaults] setObject:report forKey:@"adaos.ios.voice-ui-probe.v1"];
    [[NSUserDefaults standardUserDefaults] synchronize];
}
+ (NSDictionary *)report { return [[NSUserDefaults standardUserDefaults] dictionaryForKey:@"adaos.ios.voice-ui-probe.v1"] ?: @{}; }
@end
