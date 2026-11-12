#import "AdaAppStorage.h"
#import <unistd.h>

NSString *AdaAppStorageDirectory(void) {
    NSString *parent = NSSearchPathForDirectoriesInDomains(NSApplicationSupportDirectory, NSUserDomainMask, YES).firstObject;
    return [parent stringByAppendingPathComponent:@"AdaOSLab"];
}
BOOL AdaEnsurePrivateStorageDirectory(void) {
    NSString *path = AdaAppStorageDirectory();
    if (!path.length) return NO;
    NSFileManager *manager = [NSFileManager defaultManager];
    if (![manager fileExistsAtPath:path] && ![manager createDirectoryAtPath:path withIntermediateDirectories:YES
        attributes:@{NSFilePosixPermissions: @0700} error:nil]) return NO;
    NSDictionary *attributes = [manager attributesOfItemAtPath:path error:nil];
    return [attributes[NSFileType] isEqual:NSFileTypeDirectory] &&
        [attributes[NSFileOwnerAccountID] unsignedIntegerValue] == getuid() &&
        ([attributes[NSFilePosixPermissions] unsignedIntegerValue] & 0077) == 0;
}
