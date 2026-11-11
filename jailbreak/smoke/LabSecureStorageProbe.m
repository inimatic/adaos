#import "LabSecureStorageProbe.h"
#import <Security/Security.h>

@implementation LabSecureStorageProbe
+ (NSString *)checkRoundTrip {
    // Use a unique non-secret test item. Never query or delete session records.
    NSDictionary *query = @{
        (__bridge id)kSecClass: (__bridge id)kSecClassGenericPassword,
        (__bridge id)kSecAttrService: @"com.inimatic.adaos.lab.storage-probe",
        (__bridge id)kSecAttrAccount: [[NSUUID UUID] UUIDString]
    };
    NSData *expected = [[[NSUUID UUID] UUIDString] dataUsingEncoding:NSUTF8StringEncoding];
    NSMutableDictionary *item = [query mutableCopy];
    item[(__bridge id)kSecValueData] = expected;
    item[(__bridge id)kSecAttrAccessible] = (__bridge id)kSecAttrAccessibleWhenUnlockedThisDeviceOnly;
    OSStatus added = SecItemAdd((__bridge CFDictionaryRef)item, NULL);
    if (added != errSecSuccess) {
        return [NSString stringWithFormat:@"Secure storage unavailable (code %ld). No session was created.", (long)added];
    }
    NSMutableDictionary *lookup = [query mutableCopy];
    lookup[(__bridge id)kSecReturnData] = @YES;
    lookup[(__bridge id)kSecMatchLimit] = (__bridge id)kSecMatchLimitOne;
    CFTypeRef found = NULL;
    OSStatus read = SecItemCopyMatching((__bridge CFDictionaryRef)lookup, &found);
    id actual = CFBridgingRelease(found);
    BOOL matches = [actual isKindOfClass:[NSData class]] && [(NSData *)actual isEqualToData:expected];
    OSStatus removed = SecItemDelete((__bridge CFDictionaryRef)query);
    if (removed != errSecSuccess) {
        return [NSString stringWithFormat:@"Test record cleanup failed (code %ld). No session was created.", (long)removed];
    }
    if (read != errSecSuccess || !matches) {
        return [NSString stringWithFormat:@"Secure storage read-back failed (code %ld). Test record removed.", (long)read];
    }
    CFTypeRef remaining = NULL;
    OSStatus absent = SecItemCopyMatching((__bridge CFDictionaryRef)lookup, &remaining);
    if (remaining) {
        CFRelease(remaining);
    }
    if (absent != errSecItemNotFound) {
        return [NSString stringWithFormat:@"Test record removal could not be verified (code %ld).", (long)absent];
    }
    return @"Secure storage OK. Test value saved, read and removed. No account or session was created.";
}
@end
