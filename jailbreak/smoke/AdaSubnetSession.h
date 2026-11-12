#import <Foundation/Foundation.h>
@interface AdaSubnetSession : NSObject
+ (BOOL)safeID:(id)value;
+ (BOOL)valid:(NSDictionary *)session;
+ (NSDictionary *)read:(NSInteger *)status;
+ (BOOL)save:(NSDictionary *)session status:(NSInteger *)status;
+ (BOOL)forget:(NSInteger *)status;
+ (NSString *)endpointID;
@end
