#!/usr/bin/python3
"""
Driver for a trinamic tmc5130-bob on a raspberry pi using SPI.

This code is only raspberry pi specific because it uses pigpio, the vast majority of the code will work over any spi driver.

This used the 5160 example code (http://blog.trinamic.com/2018/02/19/stepper-motor-with-tmc5160/) as initial guidance but is mostly
written from info in the TMC5130 datasheet (https://www.trinamic.com/fileadmin/assets/Products/ICs_Documents/TMC5130_datasheet_Rev1.15.pdf)

"""
import logging
import pigpio
import time
import sys
from collections import OrderedDict

import trinamicDriver, tmc5130regs

motor28BYJ_48={
    'stepsPerRev': 2048/12,  # motor and gearbox with 12:1 speedup to second hand
    'maxrpm'     : 220       # 1 rpm is 1 rotation of second hand per minute
}

regorder=('stepsPerRev', 'maxrpm')

class tmc5130():
    """
    A class specific to the TMC5130 chip. The detailed register definitions are held in the tmc5130regs module.
    """
    def __init__(self, clockfrequ=15000000, settings=motor28BYJ_48, pigio=None, loglvl=logging.DEBUG):
        """
        sets up a motor driver for the trinamic tm,c5130
        
        clockfrequ   : clock frequency (generated by the RPi and passed to the chip, 10MHz - 16MHz recommended in manual
        
        settings     : a bunch of settings for the registers in the driver chip and some for this driver that override the default values
        
        pigio        : an instance of pigpio to use for communication with the trinamic chip, if None an instance is created
        """
        logging.basicConfig(
            level=loglvl, 
            format='%(asctime)s %(levelname)7s (%(process)d)%(threadName)12s  %(module)s.%(funcName)s: %(message)s',
            datefmt="%H:%M:%S")
        if pigio is None:
            self.pg=pigpio.pi()
            self.mypio=True
        else:
            self.pg=pigio
            self.mypio=False
        if not self.pg.connected:
            logging.getLogger().critical("pigpio daemon does not appear to be running")
            sys.exit(1)
        self.settings=settings
        self.uSC=256                 # microsteps per full step - 256 unless you do wierd stuff to the chip
        self.clockfrequ=clockfrequ
        self.ustepsPerRev=self.settings['stepsPerRev']*self.uSC
        self.tconst=self.clockfrequ/2**24
        self.maxV=round(self.RPMtoVREG(self.settings['maxrpm']))
        self.md=trinamicDriver.TrinamicDriver(clockfrequ=self.clockfrequ, datarate=1000000, pigp=self.pg,
                motordef=tmc5130regs.tmc5130, drvenpin=12, spiChannel=1, loglvl=loglvl )
        regsettings=OrderedDict((
                ('GSTAT',0),
                ('GCONF',4),
                ('CHOPCONF', 0x000100C3),
                ('IHOLD_IRUN', 0x00080F0A),
                ('TPOWERDOWN', 0x0000000A),
                ('TPWMTHRS', 0x000001F4),
                ('VSTART', 1),
                ('A1', 1500),
                ('V1', 100000),
                ('AMAX', 1000),
                ('VMAX', self.maxV),
                ('DMAX', 1100),
                ('D1', 600),
                ('VSTOP', 10),
                ('RAMPMODE',0)
                 ))
        regactions='RUWWWWWWWWWWWWW'
        assert len(regsettings)==len(regactions)
        currently=self.md.readWriteMultiple(regsettings,regactions)

    def RPMtoVREG(self, rpm):
        """
        calculates reg value (e.g. VMAX) for a given rpm
        """
        return (rpm*self.ustepsPerRev/60) / self.tconst

    def updateSettings(self, updates):
        """
        updates registers / settings in this instance and for chipset registers
        """
        pendregs=[]
        if 'stepsPerRev' in upates:
            self.settings['stepsPerRev'] = updates['stepsPerRev']
            self.ustepsPerRev=self.settings['stepsPerRev']*self.uSC
            self.maxV=round(self.RPMtoVREG(self.settings['maxrpm']))
            if 'VMAX' in self.md.lastwritten and self.md.lastwritten['VMAX'] > self.maxV:
                pendregs.append('VMAX')
        if 'maxrpm' in updates:
            self.settings['maxrpm']=updates['maxrpm']
            self.maxV=round(self.RPMtoVREG(self.settings['maxrpm']))
            if 'VMAX' in self.md.lastwritten and self.md.lastwritten['VMAX'] > self.maxV and not 'VMAX' in pendregs:
                pendregs.append('VMAX')
        
    def wait_reached(self, ticktime=.5):
        time.sleep(ticktime)
        reads={'VACTUAL':0, 'XACTUAL':0, 'XTARGET':0, 'GSTAT':0, 'RAMPSTAT':0}
        self.md.readWriteMultiple(reads, 'R')
        print('check status %x' % self.md.motordef['statusNames']['at position'])
        while self.md.status & self.md.motordef['statusNames']['at position'] == 0:
            print('loc    {location:9.2f}   chipVelocity  {velocity:9.2f}'.format(location=reads['XACTUAL']/self.ustepsPerRev, velocity=reads['VACTUAL']))
            print('ramp status: %s' % ', '.join(self.md.flagsToText(reads['RAMPSTAT'],'rampstatBits')))
            time.sleep(ticktime)
            self.md.readWriteMultiple(reads, 'R')
        rstat=', '.join(self.md.flagsToText(reads['RAMPSTAT'], 'rampstatBits'))
        print('target %9.2f reached, status %x, ramp status %s' % (reads['XACTUAL']/self.ustepsPerRev, self.md.status, rstat))

    def waitStop(self, ticktime):
        time.sleep(ticktime)
        while self.md.readInt('VACTUAL') != 0:
            time.sleep(ticktime)

    def goto(self, targetpos, wait=True):
        self.md.enableOutput(True)
        self.md.writeInt('XTARGET',int(self.ustepsPerRev*targetpos))
        if wait:
            self.wait_reached()
        self.md.enableOutput(False)

    def stop(self):
        self.md.writeInt('XTARGET', self.md.readInt('XACTUAL'))
        self.md.writeInt('VMAX', self.maxV)
        self.md.writeInt('RAMPMODE',0)
        self.waitStop(ticktime=.1)
        self.md.enableOutput(False)

    def close(self):
        self.md.close()
        if self.mypio:
            self.pg.stop()
        self.pg=None
