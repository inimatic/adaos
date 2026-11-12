#import "AdaSubnetSession.h"
#import <Security/Security.h>

static NSDictionary *Query(void) {
    return @{(__bridge id)kSecClass: (__bridge id)kSecClassGenericPassword,
        (__bridge id)kSecAttrService: @"com.inimatic.adaos.client.subnet",
        (__bridge id)kSecAttrAccount: @"approved-client-v1"};
}
@implementation AdaSubnetSession
+ (BOOL)safeID:(id)value {
    if (![value isKindOfClass:[NSString class]] || [value length] < 1 || [value length] > 128) return NO;
    NSCharacterSet *allowed = [NSCharacterSet characterSetWithCharactersInString:@"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"];
    return [value rangeOfCharacterFromSet:allowed.invertedSet].location == NSNotFound;
}
+ (BOOL)valid:(NSDictionary *)session {
    if (![session isKindOfClass:[NSDictionary class]] || ![session[@"schema"] isEqual:@"adaos.ios.client-session.v1"] ||
        ![@[@"ru", @"global"] containsObject:session[@"zone"]] || ![self safeID:session[@"subnet_id"]] ||
        ![self safeID:session[@"webspace_id"]] || ![self safeID:session[@"endpoint_id"]]) return NO;
    NSString *token = session[@"session_jwt"];
    if (![token isKindOfClass:[NSString class]] || token.length < 16 || token.length > 8192 ||
        [token componentsSeparatedByString:@"."].count != 3) return NO;
    NSCharacterSet *allowed = [NSCharacterSet characterSetWithCharactersInString:@"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-."];
    return [token rangeOfCharacterFromSet:allowed.invertedSet].location == NSNotFound;
}
+ (NSDictionary *)read:(NSInteger *)status {
    NSMutableDictionary *query = [Query() mutableCopy];
    query[(__bridge id)kSecReturnData] = @YES; query[(__bridge id)kSecMatchLimit] = (__bridge id)kSecMatchLimitOne;
    CFTypeRef found = NULL;
    *status = SecItemCopyMatching((__bridge CFDictionaryRef)query, &found);
    id data = CFBridgingRelease(found);
    if (*status != errSecSuccess) return nil;
    id session = [data isKindOfClass:[NSData class]] && [data length] <= 16384 ?
        [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
    if (![self valid:session]) { *status = errSecDecode; return nil; }
    return session;
}
+ (BOOL)save:(NSDictionary *)session status:(NSInteger *)status {
    if (![self valid:session]) { *status = errSecParam; return NO; }
    NSData *data = [NSJSONSerialization dataWithJSONObject:session options:0 error:nil];
    if (!data || data.length > 16384) { *status = errSecParam; return NO; }
    NSMutableDictionary *item = [Query() mutableCopy];
    item[(__bridge id)kSecValueData] = data;
    item[(__bridge id)kSecAttrAccessible] = (__bridge id)kSecAttrAccessibleWhenUnlockedThisDeviceOnly;
    *status = SecItemAdd((__bridge CFDictionaryRef)item, NULL);
    if (*status != errSecSuccess) return NO; /* No implicit overwrite. */
    NSInteger readStatus;
    NSDictionary *saved = [self read:&readStatus];
    if (![saved isEqual:session]) { SecItemDelete((__bridge CFDictionaryRef)Query()); *status = errSecDecode; return NO; }
    return YES;
}
+ (BOOL)forget:(NSInteger *)status {
    *status = SecItemDelete((__bridge CFDictionaryRef)Query());
    return *status == errSecSuccess || *status == errSecItemNotFound;
}
+ (NSString *)endpointID {
    NSUserDefaults *defaults = [NSUserDefaults standardUserDefaults];
    NSString *value = [defaults stringForKey:@"adaos.ios.endpoint-id"];
    if (![self safeID:value]) {
        value = [@"ios-" stringByAppendingString:[NSUUID UUID].UUIDString.lowercaseString];
        [defaults setObject:value forKey:@"adaos.ios.endpoint-id"]; [defaults synchronize];
    }
    return value;
}
@end
