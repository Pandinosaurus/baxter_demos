#!/usr/bin/env python

"""Visual Servo-er by Jackie Kay
This node listens for the centroid of the desired object from object_finder, converts to
robot coordinates, and commands the hand to into grasping range
"""

# TODO:
# Get out of invalid joint position states

import sys
import argparse

import rospy
import baxter_interface

import cv, cv2, cv_bridge
import numpy
import tf

import common
import ik_command

from baxter_demos.msg import (
    BlobInfo
)

from sensor_msgs.msg import (
    Image, CameraInfo, Range
)

from geometry_msgs.msg import(Point)

#TODO: Parametrize
class VisualCommand():
    def __init__(self, iksvc, limb):
        #Do some stuff
        self.centroid = None
        self.x_extremes = None
        self.ir_reading = None
        self.cam_matrix = None
        self.iksvc = iksvc
        self.limb = limb
        self.limb_iface = baxter_interface.Limb(limb)
        self.tf_listener = tf.TransformListener()
        self.range_limit = 0.1
        self.stateidx = 0
        self.states = self.wait_centroid, self.servo_xy, self.servo_z, self.done
        self.done = False
        args = [self.inc, self.min_pose_z, self.min_ir_depth]
        paramnames = ["servo_speed", "min_pose_z", "min_ir_depth"]
        for arg, param in zip(args, paramnames):
            arg = rospy.get_param(param)
        self.goal_pos = (rospy.get_param("camera_x")*float(rospy.get_param("goal_ratio_x")), rospy.get_param("camera_y")*float(rospy.get_param("goal_ratio_y")))


    def subscribe(self):
        topic = "object_tracker/"+self.limb+"/centroid"
        self.centroid_sub = rospy.Subscriber(topic, BlobInfo, self.centroid_callback)
        topic = "/robot/range/"+self.limb+"_hand_range/state"
        self.ir_sub = rospy.Subscriber(topic, Range, self.ir_callback)
        topic = "/cameras/"+self.limb+"_hand_camera/camera_info"
        #self.info_sub = rospy.Subscriber(topic, CameraInfo, self.info_callback)

    def command_ik(self, direction):
        """Use the Rethink IK service to figure out a desired joint position"""
        end_pose = self.limb_iface.endpoint_pose()
        current_p = numpy.array(end_pose['position']+end_pose['orientation']) 
        direction = numpy.concatenate((direction, numpy.zeros(4)))
        desired_p = current_p + direction
        ik_command.service_request(self.iksvc, desired_p, self.limb)

    def wait_centroid(self):
        pass

    def servo_xy(self):
        d = self.centroid - self.goal_pos

        (trans, rot) = self.tf_listener.lookupTransform('/'+self.limb+'_hand_camera', '/base', rospy.Time(0))
        R = tf.transformations.quaternion_matrix(rot)[:3, :3]
        d = numpy.concatenate( (d, numpy.zeros(1)) )
        d_rot = numpy.dot(R, d) 
        direction = inc*d_rot / numpy.linalg.norm(d_rot)
        if not self.outOfRange():
            direction[2] = inc
        else:
            direction[2] = 0

        self.command_ik(direction)

    def servo_z(self):
        #Calculate z-translate
        d = self.centroid - self.goal_pos

        direction = numpy.array([0, 0, -inc])
        self.command_ik(direction)

    def done(self):
        self.done = True
        
    def outOfRange(self):
        return (self.limb_iface.endpoint_pose()['position'][2] >= min_pose_z) and (self.ir_reading >= min_ir_depth)

    def centroid_callback(self, data):
        self.centroid = numpy.array((data.centroid.x, data.centroid.y))
        self.x_extremes = (data.xmin, data.xmax)

        if self.centroid[0] == -1 or self.centroid[1] == -1:
            print "Waiting on centroid from object_finder"
            self.stateidx = 0
            return
        
        d = self.centroid - self.goal_pos

        #Maybe experiment with making this proportional to Z-coordinate
        threshold = (self.x_extremes[1] - self.x_extremes[0])*0.08
        if abs(d[0]) > threshold and abs(d[1]) > threshold:
            self.stateidx = 1
        elif self.outOfRange(): 
            self.stateidx = 2
        else:
            self.stateidx = 3
        
        self.states[self.stateidx]()

    def ir_callback(self, data):
        self.ir_reading = data.range

    def info_callback(self, data):
        self.cam_matrix = numpy.array(data.P).reshape(3, 4) #3x4 Projection matrix
        self.cam_matrix = self.cam_matrix[:, :3]
        print self.cam_matrix
        # Given a 3D point [X Y Z]', the projection (x, y) of the point onto
        #  the rectified image is given by:
        #  [u v w]' = P * [X Y Z 1]'
        #         x = u / w
        #         y = v / w
        self.info_sub.unregister() #Only subscribe once: might modify this
        

def main():
    arg_fmt = argparse.RawDescriptionHelpFormatter
    parser = argparse.ArgumentParser(formatter_class=arg_fmt,
                                     description=main.__doc__)
    required = parser.add_argument_group('required arguments')
    required.add_argument(
        '-l', '--limb', required=True, choices=['left', 'right'],
        help='send joint trajectory to which limb'
    )

    args = parser.parse_args(rospy.myargv()[1:])
    limb = args.limb

    rospy.init_node("visual_servo")

    #Bit of cheating: move the robot's arms to a neutral position before running

    dominant_joints=[1.187301128466797, 1.942403170440674, 0.08206797205810547, -0.996704015789795, -0.6734175651123048, 1.0266166411193849, 0.4985437554931641]

    off_joints = [-1.1255584018249511, 1.4522963092712404, 0.6354515406555176, -0.8843399232055664, 0.6327670742797852, 1.2751215284729005, -0.4084223843078614, ]
    

    names = ['e0', 'e1', 's0', 's1', 'w0', 'w1', 'w2']
    off_limb = "right"
    if limb == "right": off_limb = "left"
    off_names = [off_limb+"_"+name for name in names]
    
    jointlists = {}
    jointlists[off_limb] = off_joints
    jointlists[limb] = dominant_joints

    for side in [limb, off_limb]:
        limb_if = baxter_interface.Limb(side)
        limb_names = [side+"_"+name for name in names]
        joint_commands = dict(zip(limb_names, jointlists[side] ))
        limb_if.move_to_joint_positions(joint_commands)

    #Calibrate gripper
    gripper_if = baxter_interface.Gripper(limb)
    gripper_if.calibrate()
    
    iksvc, ns = ik_command.connect_service(limb)

    command = VisualCommand(iksvc, limb)
    command.subscribe()
    while not rospy.is_shutdown() and not command.done:
        pass

if __name__ == "__main__":
    main()