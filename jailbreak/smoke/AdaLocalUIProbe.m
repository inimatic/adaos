#import "AdaLocalUIProbe.h"
#import "AdaAppStorage.h"
#import <fcntl.h>
#import <unistd.h>
#import <math.h>
static NSString *const ReportKey = @"adaos.ios.local-ui-probe.v1";
static NSString *Path(void) { return [AdaAppStorageDirectory() stringByAppendingPathComponent:@"local-ui-probe.json"]; }
@implementation AdaLocalUIProbe
+ (BOOL)validJob:(id)job now:(NSTimeInterval)now {
    return [job isKindOfClass:[NSDictionary class]] && [job count] == 2 &&
        [job[@"operation"] isEqual:@"keyboard-and-speech-check"] && [job[@"expires_at"] isKindOfClass:[NSNumber class]] &&
        isfinite([job[@"expires_at"] doubleValue]) && [job[@"expires_at"] doubleValue] > now && [job[@"expires_at"] doubleValue] <= now + 120;
}
+ (BOOL)prepare {
    if (getuid() != 501 || !AdaEnsurePrivateStorageDirectory()) return NO;
    NSData *data = [NSJSONSerialization dataWithJSONObject:@{@"operation": @"keyboard-and-speech-check", @"expires_at": @([NSDate date].timeIntervalSince1970 + 90)} options:0 error:nil];
    if (!data) return NO;
    int fd = open(Path().fileSystemRepresentation, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
    if (fd < 0) return NO;
    BOOL ok = write(fd, data.bytes, data.length) == (ssize_t)data.length; close(fd);
    if (!ok) unlink(Path().fileSystemRepresentation);
    if (ok) { [[NSUserDefaults standardUserDefaults] setObject:@{@"prepared": @YES} forKey:ReportKey]; [[NSUserDefaults standardUserDefaults] synchronize]; }
    return ok;
}
+ (BOOL)consume {
    if (!AdaEnsurePrivateStorageDirectory()) return NO;
    NSFileManager *manager = [NSFileManager defaultManager]; NSDictionary *attr = [manager attributesOfItemAtPath:Path() error:nil];
    if (![attr[NSFileType] isEqual:NSFileTypeRegular] || [attr[NSFileSize] unsignedLongLongValue] > 512 ||
        [attr[NSFileOwnerAccountID] unsignedIntegerValue] != getuid() || ([attr[NSFilePosixPermissions] unsignedIntegerValue] & 0077)) return NO;
    NSData *data = [NSData dataWithContentsOfFile:Path()]; id job = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
    return [manager removeItemAtPath:Path() error:nil] && [self validJob:job now:[NSDate date].timeIntervalSince1970];
}
+ (void)saveKeyboardShown:(BOOL)shown dismissed:(BOOL)dismissed restored:(BOOL)restored {
    [[NSUserDefaults standardUserDefaults] setObject:@{@"prepared": @NO, @"keyboard_shown": @(shown), @"keyboard_dismissed": @(dismissed), @"controls_restored": @(restored)} forKey:ReportKey];
    [[NSUserDefaults standardUserDefaults] synchronize];
}
+ (NSDictionary *)report {
    id stored = [[NSUserDefaults standardUserDefaults] objectForKey:ReportKey];
    NSMutableDictionary *safe = [NSMutableDictionary dictionary];
    if ([stored isKindOfClass:[NSDictionary class]]) for (NSString *key in @[@"prepared", @"keyboard_shown", @"keyboard_dismissed", @"controls_restored"])
        if ([stored[key] isKindOfClass:[NSNumber class]]) safe[key] = @([stored[key] boolValue]);
    return safe;
}
@end
