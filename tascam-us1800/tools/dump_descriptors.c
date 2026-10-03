#include <IOKit/IOKitLib.h>
#include <IOKit/usb/IOUSBLib.h>
#include <IOKit/IOCFPlugIn.h>
#include <CoreFoundation/CoreFoundation.h>
#include <stdio.h>
#include <stdlib.h>

#define VID 0x0644
#define PID 0x8030

int main(void) {
    CFMutableDictionaryRef match = IOServiceMatching(kIOUSBDeviceClassName);
    int32_t vid = VID, pid = PID;
    CFNumberRef vr = CFNumberCreate(NULL, kCFNumberSInt32Type, &vid);
    CFNumberRef pr = CFNumberCreate(NULL, kCFNumberSInt32Type, &pid);
    CFDictionarySetValue(match, CFSTR(kUSBVendorID), vr);
    CFDictionarySetValue(match, CFSTR(kUSBProductID), pr);
    CFRelease(vr); CFRelease(pr);

    io_service_t svc = IOServiceGetMatchingService(kIOMainPortDefault, match);
    if (!svc) {
        printf("Device not found!\n");
        return 1;
    }

    IOCFPlugInInterface **plug = NULL;
    SInt32 score;
    IOCreatePlugInInterfaceForService(svc, kIOUSBDeviceUserClientTypeID,
                                      kIOCFPlugInInterfaceID, &plug, &score);
    IOObjectRelease(svc);
    if (!plug) {
        printf("Failed to create plugin interface\n");
        return 1;
    }

    IOUSBDeviceInterface300 **dev = NULL;
    (*plug)->QueryInterface(plug, CFUUIDGetUUIDBytes(kIOUSBDeviceInterfaceID300),
                            (LPVOID *)&dev);
    (*plug)->Release(plug);
    if (!dev) {
        printf("Failed to query device interface\n");
        return 1;
    }

    UInt8 numConfig;
    (*dev)->GetNumberOfConfigurations(dev, &numConfig);
    printf("Number of configurations: %d\n", numConfig);

    kern_return_t kr = (*dev)->USBDeviceOpen(dev);
    printf("USBDeviceOpen: 0x%x\n", kr);
    kr = (*dev)->SetConfiguration(dev, 1);
    printf("SetConfiguration: 0x%x\n", kr);

    IOUSBConfigurationDescriptorPtr cd;
    (*dev)->GetConfigurationDescriptorPtr(dev, 0, &cd);
    if (cd) {
        printf("Config total length: %d, num interfaces: %d\n",
               cd->wTotalLength, cd->bNumInterfaces);
    }

    IOUSBFindInterfaceRequest req = {
        kIOUSBFindInterfaceDontCare, kIOUSBFindInterfaceDontCare,
        kIOUSBFindInterfaceDontCare, kIOUSBFindInterfaceDontCare
    };
    io_iterator_t iter;
    (*dev)->CreateInterfaceIterator(dev, &req, &iter);

    io_service_t iface_svc;
    int iface_idx = 0;
    while ((iface_svc = IOIteratorNext(iter))) {
        IOCFPlugInInterface **i_plug = NULL;
        IOCreatePlugInInterfaceForService(iface_svc, kIOUSBInterfaceUserClientTypeID,
                                          kIOCFPlugInInterfaceID, &i_plug, &score);
        IOObjectRelease(iface_svc);
        if (i_plug) {
            IOUSBInterfaceInterface300 **iface = NULL;
            (*i_plug)->QueryInterface(i_plug, CFUUIDGetUUIDBytes(kIOUSBInterfaceInterfaceID300),
                                      (LPVOID *)&iface);
            (*i_plug)->Release(i_plug);
            if (iface) {
                (*iface)->USBInterfaceOpen(iface);
                UInt8 ifNum, altSetting;
                (*iface)->GetInterfaceNumber(iface, &ifNum);
                (*iface)->GetAlternateSetting(iface, &altSetting);
                printf("\nInterface %d (number %d, current alt %d)\n", iface_idx, ifNum, altSetting);

                for (UInt8 alt = 0; alt < 4; alt++) {
                    kern_return_t akr = (*iface)->SetAlternateInterface(iface, alt);
                    if (akr != kIOReturnSuccess) continue;
                    UInt8 numPipes;
                    (*iface)->GetNumEndpoints(iface, &numPipes);
                    printf("  -> AltSetting %d: %d endpoints\n", alt, numPipes);

                    for (UInt8 p = 1; p <= numPipes; p++) {
                        UInt8 dir, num, tt, interval;
                        UInt16 maxPkt;
                        (*iface)->GetPipeProperties(iface, p, &dir, &num, &tt, &maxPkt, &interval);
                        const char *tt_str = "Control";
                        if (tt == kUSBAnyDirn || tt == kUSBIsoc) tt_str = "Isoc";
                        else if (tt == kUSBBulk) tt_str = "Bulk";
                        else if (tt == kUSBInterrupt) tt_str = "Interrupt";
                        printf("     Pipe %d: addr=0x%02X dir=%s type=%s maxPkt=%d interval=%d\n",
                               p, (dir == kUSBIn ? 0x80 : 0) | num,
                               dir == kUSBIn ? "IN" : "OUT", tt_str, maxPkt, interval);
                    }
                }
                (*iface)->USBInterfaceClose(iface);
                (*iface)->Release(iface);
            }
        }
        iface_idx++;
    }
    IOObjectRelease(iter);

    (*dev)->Release(dev);
    return 0;
}
