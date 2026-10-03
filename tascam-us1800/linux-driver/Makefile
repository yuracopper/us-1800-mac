obj-m += snd-usb-us1800.o
snd-usb-us1800-y := us1800.o us1800_pcm.o us1800_playback.o us1800_capture.o

all:
	make -C /lib/modules/$(shell uname -r)/build M=$(PWD) modules

clean:
	make -C /lib/modules/$(shell uname -r)/build M=$(PWD) clean
