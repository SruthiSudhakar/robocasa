import argparse
import json
import os
import random
import time

import h5py
import imageio
import numpy as np
import robosuite
from termcolor import colored

import robocasa
import pdb
from tqdm import tqdm
import shutil


'''
USAGE:
python /proj/vondrick3/sruthi/robots/robocasa/robocasa/scripts/playback_dataset.py \
    --dataset /proj/vondrick3/sruthi/robots/robocasa/datasets/v0.1/single_stage/kitchen_pnp/PnPSinkToCounter/mg/2024-05-04-22-14-34_and_2024-05-07-07-40-21/demo_gentex_im128_randcams_new_images_val_kbpckt_firsthalf.hdf5 \
    --use-actions \
    --add_raw_states

python /proj/vondrick3/sruthi/robots/robocasa/robocasa/scripts/playback_dataset.py \
    --dataset /proj/vondrick3/sruthi/robots/robocasa/datasets/v0.1/single_stage/kitchen_pnp/PnPSinkToCounter/mg/2024-05-04-22-14-34_and_2024-05-07-07-40-21/demo_gentex_im128_randcams_new_images_val_kbpckt_secondhalf.hdf5 \
    --use-actions \
    --add_raw_states
'''

def playback_trajectory_with_env(
    env,
    initial_state,
    states,
    actions=None,
    render=False,
    video_writer=None,
    video_skip=5,
    camera_names=None,
    first=False,
    verbose=False,
    add_raw_states=False,
):
    """
    Helper function to playback a single trajectory using the simulator environment.
    If @actions are not None, it will play them open-loop after loading the initial state.
    Otherwise, @states are loaded one by one.

    Args:
        env (instance of EnvBase): environment
        initial_state (dict): initial simulation state to load
        states (np.array): array of simulation states to load
        actions (np.array): if provided, play actions back open-loop instead of using @states
        render (bool): if True, render on-screen
        video_writer (imageio writer): video writer
        video_skip (int): determines rate at which environment frames are written to video
        camera_names (list): determines which camera(s) are used for rendering. Pass more than
            one to output a video with multiple camera views concatenated horizontally.
        first (bool): if True, only use the first frame of each episode.
    """
    write_video = video_writer is not None
    video_count = 0
    assert not (render and write_video)

    # load the initial state
    ## this reset call doesn't seem necessary.
    ## seems ok to remove but haven't fully tested it.
    ## removing for now
    # env.reset()

    if verbose:
        ep_meta = json.loads(initial_state["ep_meta"])
        lang = ep_meta.get("lang", None)
        if lang is not None:
            print(colored(f"Instruction: {lang}", "green"))
        print(colored("Spawning environment...", "yellow"))
    reset_to(env, initial_state)

    if states.shape[0]==1:
        traj_len = actions.shape[0]
    else:
        traj_len = states.shape[0]

    action_playback = actions is not None
    if action_playback:
        try:
            assert states.shape[0] == actions.shape[0]
        except Exception as e:
            print('HUH assert states.shape[0] == actions.shape[0]', e)


    if render is False:
        print(colored("Running episode...", "yellow"))

    keys_to_save=['robot0_eef_pos', 'obj_pos','container_pos'] #keys_to_save=['0:3', '3:7', '7:10', '10:14', '14:17','17:21','21:24','24:28']
    save_rollout_raw_obs=None
    for i in range(traj_len):
        start = time.time()

        if action_playback:
            if add_raw_states:
                get_raw_obs=env._get_observations()
                concated_current_obs=np.concatenate([get_raw_obs[key] for key in keys_to_save],axis=0)
                if save_rollout_raw_obs is None:
                    save_rollout_raw_obs = np.expand_dims(concated_current_obs,0)
                else:
                    save_rollout_raw_obs = np.vstack((save_rollout_raw_obs,np.expand_dims(concated_current_obs,0)))
            pdb.set_trace()
            start=time.time()
            env.step(actions[i])
            end=time.time()
            print('step TIME ELAPSED', end-start)
            if i < traj_len - 1 and states.shape[0]==actions.shape[0]:
                # check whether the actions deterministically lead to the same recorded states
                state_playback = np.array(env.sim.get_state().flatten())
                if not np.all(np.equal(states[i + 1], state_playback)):
                    err = np.linalg.norm(states[i + 1] - state_playback)
                    if verbose or i == traj_len - 2:
                        print(
                            colored(
                                "warning: playback diverged by {} at step {}".format(
                                    err, i
                                ),
                                "yellow",
                            )
                        )
        else:
            reset_to(env, {"states": states[i]})

        # on-screen render
        if render:
            if env.viewer is None:
                env.initialize_renderer()

            # so that mujoco viewer renders
            env.viewer.update()

            max_fr = 60
            elapsed = time.time() - start
            diff = 1 / max_fr - elapsed
            if diff > 0:
                time.sleep(diff)

        # video render
        if write_video:
            if video_count % video_skip == 0:
                video_img = []
                for cam_name in camera_names:
                    im = env.sim.render(height=512, width=512, camera_name=cam_name)[
                        ::-1
                    ]
                    video_img.append(im)
                video_img = np.concatenate(
                    video_img, axis=1
                )  # concatenate horizontally
                video_writer.append_data(video_img)

            video_count += 1

        if first:
            break

    if render:
        env.viewer.close()
        env.viewer = None
    if save_rollout_raw_obs is None:
        return []
    return save_rollout_raw_obs

def playback_trajectory_with_obs(
    traj_grp,
    video_writer,
    video_skip=5,
    image_names=None,
    first=False,
):
    """
    This function reads all "rgb" observations in the dataset trajectory and
    writes them into a video.

    Args:
        traj_grp (hdf5 file group): hdf5 group which corresponds to the dataset trajectory to playback
        video_writer (imageio writer): video writer
        video_skip (int): determines rate at which environment frames are written to video
        image_names (list): determines which image observations are used for rendering. Pass more than
            one to output a video with multiple image observations concatenated horizontally.
        first (bool): if True, only use the first frame of each episode.
    """
    assert (
        image_names is not None
    ), "error: must specify at least one image observation to use in @image_names"
    video_count = 0

    traj_len = traj_grp["obs/{}".format(image_names[0] + "_image")].shape[0]
    for i in range(traj_len):
        if video_count % video_skip == 0:
            # concatenate image obs together
            im = [traj_grp["obs/{}".format(k + "_image")][i] for k in image_names]
            frame = np.concatenate(im, axis=1)
            video_writer.append_data(frame)
        video_count += 1

        if first:
            break


def get_env_metadata_from_dataset(dataset_path, ds_format="robomimic"):
    """
    Retrieves env metadata from dataset.

    Args:
        dataset_path (str): path to dataset

    Returns:
        env_meta (dict): environment metadata. Contains 3 keys:

            :`'env_name'`: name of environment
            :`'type'`: type of environment, should be a value in EB.EnvType
            :`'env_kwargs'`: dictionary of keyword arguments to pass to environment constructor
    """
    dataset_path = os.path.expanduser(dataset_path)
    f = h5py.File(dataset_path, "r")
    if ds_format == "robomimic":
        env_meta = json.loads(f["data"].attrs["env_args"])
    else:
        raise ValueError
    f.close()
    return env_meta


class ObservationKeyToModalityDict(dict):
    """
    Custom dictionary class with the sole additional purpose of automatically registering new "keys" at runtime
    without breaking. This is mainly for backwards compatibility, where certain keys such as "latent", "actions", etc.
    are used automatically by certain models (e.g.: VAEs) but were never specified by the user externally in their
    config. Thus, this dictionary will automatically handle those keys by implicitly associating them with the low_dim
    modality.
    """

    def __getitem__(self, item):
        # If a key doesn't already exist, warn the user and add default mapping
        if item not in self.keys():
            print(
                f"ObservationKeyToModalityDict: {item} not found,"
                f" adding {item} to mapping with assumed low_dim modality!"
            )
            self.__setitem__(item, "low_dim")
        return super(ObservationKeyToModalityDict, self).__getitem__(item)


def reset_to(env, state):
    """
    Reset to a specific simulator state.

    Args:
        state (dict): current simulator state that contains one or more of:
            - states (np.ndarray): initial state of the mujoco environment
            - model (str): mujoco scene xml

    Returns:
        observation (dict): observation dictionary after setting the simulator state (only
            if "states" is in @state)
    """
    should_ret = False
    if "model" in state:
        if state.get("ep_meta", None) is not None:
            # set relevant episode information
            ep_meta = json.loads(state["ep_meta"])
        else:
            ep_meta = {}
        if hasattr(env, "set_attrs_from_ep_meta"):  # older versions had this function
            env.set_attrs_from_ep_meta(ep_meta)
        elif hasattr(env, "set_ep_meta"):  # newer versions
            env.set_ep_meta(ep_meta)
        # this reset is necessary.
        # while the call to env.reset_from_xml_string does call reset,
        # that is only a "soft" reset that doesn't actually reload the model.
        start=time.time()
        env.reset()
        end=time.time()
        print('reset TIME ELAPSED', end-start)

        object_configured = True
        for obj in ep_meta['object_cfgs']:
            if obj['name']=='obj':
                if obj['info'] is None:
                    object_configured=False
                    print('RELOADING XML')
        if object_configured:
            robosuite_version_id = int(robosuite.__version__.split(".")[1])
            if robosuite_version_id <= 3:
                from robosuite.utils.mjcf_utils import postprocess_model_xml

                xml = postprocess_model_xml(state["model"])
            else:
                # v1.4 and above use the class-based edit_model_xml function
                xml = env.edit_model_xml(state["model"])
            env.reset_from_xml_string(xml)
        env.sim.reset()
        # hide teleop visualization after restoring from model
        # env.sim.model.site_rgba[env.eef_site_id] = np.array([0., 0., 0., 0.])
        # env.sim.model.site_rgba[env.eef_cylinder_id] = np.array([0., 0., 0., 0.])
    elif state.get("ep_meta", None) is not None:
        ep_meta = json.loads(state["ep_meta"])
        if hasattr(env, "set_attrs_from_ep_meta"):  # older versions had this function
            env.set_attrs_from_ep_meta(ep_meta)
        elif hasattr(env, "set_ep_meta"):  # newer versions
            env.set_ep_meta(ep_meta)
        print('RELOADING XML')
        env.reset()
        env.sim.reset()

    if "states" in state:
        env.sim.set_state_from_flattened(state["states"])
        env.sim.forward()
        should_ret = True

    # update state as needed
    if hasattr(env, "update_sites"):
        # older versions of environment had update_sites function
        env.update_sites()
    if hasattr(env, "update_state"):
        # later versions renamed this to update_state
        env.update_state()

    # if should_ret:
    #     # only return obs if we've done a forward call - otherwise the observations will be garbage
    #     return get_observation()
    return None


def playback_dataset(args):
    # some arg checking
    write_video = args.render is not True
    if args.video_path is None:
        args.video_path = args.dataset.split(".hdf5")[0] + ".mp4"
        if args.use_actions:
            args.video_path = args.dataset.split(".hdf5")[0] + "_use_actions.mp4"
        elif args.use_abs_actions:
            args.video_path = args.dataset.split(".hdf5")[0] + "_use_abs_actions.mp4"
        elif args.use_obs:
            args.video_path = args.dataset.split(".hdf5")[0] + "_use_obs.mp4"
        else:
            args.video_path = args.dataset.split(".hdf5")[0] + "_use_storedstates.mp4"
    assert not (args.render and write_video)  # either on-screen or video but not both

    # Auto-fill camera rendering info if not specified
    if args.render_image_names is None:
        # We fill in the automatic values
        env_meta = get_env_metadata_from_dataset(dataset_path=args.dataset)
        args.render_image_names = "robot0_agentview_center"

    if args.render:
        # on-screen rendering can only support one camera
        assert len(args.render_image_names) == 1

    if args.use_obs:
        assert write_video, "playback with observations can only write to video"
        assert (
            not args.use_actions and not args.use_abs_actions
        ), "playback with observations is offline and does not support action playback"

    env = None

    # create environment only if not playing back with observations
    if not args.use_obs:
        # # need to make sure ObsUtils knows which observations are images, but it doesn't matter
        # # for playback since observations are unused. Pass a dummy spec here.
        # dummy_spec = dict(
        #     obs=dict(
        #             low_dim=["robot0_eef_pos"],
        #             rgb=[],
        #         ),
        # )
        # initialize_obs_utils_with_obs_specs(obs_modality_specs=dummy_spec)

        env_meta = get_env_metadata_from_dataset(dataset_path=args.dataset)
        if args.use_abs_actions:
            env_meta["env_kwargs"]["controller_configs"][
                "control_delta"
            ] = False  # absolute action space

        env_kwargs = env_meta["env_kwargs"]
        env_kwargs["env_name"] = env_meta["env_name"]
        env_kwargs["has_renderer"] = False
        env_kwargs["renderer"] = "mjviewer"
        env_kwargs["has_offscreen_renderer"] = write_video
        env_kwargs["use_camera_obs"] = False

        if args.verbose:
            print(
                colored(
                    "Initializing environment for {}...".format(env_kwargs["env_name"]),
                    "yellow",
                )
            )

        env = robosuite.make(**env_kwargs)

    f = h5py.File(args.dataset, "r")
    if args.add_raw_states:
        assert args.use_actions
        new_dataset = h5py.File(f'{args.dataset[:-5]}_classifier.hdf5', "r+")

    # list of all demonstration episodes (sorted in increasing number order)
    if args.filter_key is not None:
        print("using filter key: {}".format(args.filter_key))
        demos = [
            elem.decode("utf-8")
            for elem in np.array(f["mask/{}".format(args.filter_key)])
        ]
    elif "data" in f.keys():
        demos = list(f["data"].keys())

    inds = np.argsort([int(elem[5:]) for elem in demos])
    demos = [demos[i] for i in inds]

    # maybe reduce the number of demonstrations to playback
    if args.n is not None:
        # random.shuffle(demos)
        demos = demos[: args.n]

    # maybe dump video
    video_writer = None
    if write_video:
        video_writer = imageio.get_writer(args.video_path, fps=20)

    for ind in range(len(demos)):
        ep = demos[ind]
        if args.demos and ep not in args.demos:
            continue
        if not args.use_obs and 'combined' in args.dataset:
            try:
                env_kwargs['env_name']=f['data/{}'.format(ep)].attrs['dataset_path'].split('/proj/vondrick3/sruthi/robots/robocasa/datasets/v0.1/single_stage/kitchen_pnp/')[1].split('/')[0]
            except:
                print('SETTING ENV_KWARGS TO PNPSINKTOCOUNTER')
                env_kwargs['env_name']='PnPSinkToCounter'
            env = robosuite.make(**env_kwargs)
        print(colored("\nPlaying back episode: {}".format(ep), "yellow"))
        # print(colored("\nPlaying back episode: {}, OG EP {}".format(ep, f['data'][ep].attrs['og_demo_id']), "yellow"))

        if args.use_obs:
            playback_trajectory_with_obs(
                traj_grp=f["data/{}".format(ep)],
                video_writer=video_writer,
                video_skip=args.video_skip,
                image_names=args.render_image_names,
                first=args.first,
            )
            continue

        # prepare initial state to reload from
        states = f["data/{}/states".format(ep)][()]
        initial_state = dict(states=states[0])
        initial_state["model"] = f["data/{}".format(ep)].attrs["model_file"]
        ep_meta_modified = json.loads(f["data/{}".format(ep)].attrs.get("ep_meta", None))
        # for obj in ep_meta_modified['object_cfgs']:
        #     if obj['name']=='obj':
        #         temp_info = obj.pop('info',None)
        #         obj['split']='B'
        #         obj['obj_groups']=temp_info['cat']
                # print('CANGING THE OBJECT', obj['info']['cat'])
                # obj.pop('info',None)
                # obj.pop('cookable',None)
                # obj.pop('microwavable',None)
                # obj.pop('washable',None)
                # obj.pop('freezable',None)
                # env_name=env_kwargs["env_name"]
                # obj['exclude_obj_groups']=f"{env_name}_seen"
        initial_state["ep_meta"] = json.dumps(ep_meta_modified)
        if args.extend_states:
            states = np.concatenate((states, [states[-1]] * 50))

        # supply actions if using open-loop action playback
        actions = None
        assert not (
            args.use_actions and args.use_abs_actions
        )  # cannot use both relative and absolute actions
        if args.use_actions:
            actions = f["data/{}/actions".format(ep)][()]
        elif args.use_abs_actions:
            actions = f["data/{}/actions_abs".format(ep)][()]  # absolute actions

        save_rollout_raw_obs = playback_trajectory_with_env(
            env=env,
            initial_state=initial_state,
            states=states,
            actions=actions,
            render=args.render,
            video_writer=video_writer,
            video_skip=args.video_skip,
            camera_names=args.render_image_names,
            first=args.first,
            verbose=args.verbose,
            add_raw_states=args.add_raw_states
        )
        if args.add_raw_states and save_rollout_raw_obs is not None:
            try:
                assert save_rollout_raw_obs.shape==(actions.shape[0],9)
                assert np.allclose(save_rollout_raw_obs[:, :3], new_dataset['data'][ep]['obs']['robot0_eef_pos'][:], rtol=0, atol=1e-1)
            except:
                print('something fishy')
                print('something fishy')
            if 'raw_obs' in new_dataset['data'][ep]:
                del new_dataset['data'][ep]['raw_obs']
            new_dataset['data'][ep].create_dataset('raw_obs', data=save_rollout_raw_obs)

    if args.add_raw_states:
        new_dataset.close()
        print(f'{args.dataset[:-5]}_classifier.hdf5')
    f.close()
    if write_video:
        print(colored(f"Saved video to {args.video_path}", "green"))
        video_writer.close()

    if env is not None:
        env.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=str,
        help="path to hdf5 dataset",
    )
    parser.add_argument(
        "--filter_key",
        type=str,
        default=None,
        help="(optional) filter key, to select a subset of trajectories in the file",
    )

    # number of trajectories to playback. If omitted, playback all of them.
    parser.add_argument(
        "--n",
        type=int,
        default=None,
        help="(optional) stop after n trajectories are played",
    )

    # Use image observations instead of doing playback using the simulator env.
    parser.add_argument(
        "--use-obs",
        action="store_true",
        help="visualize trajectories with dataset image observations instead of simulator",
    )

    # Playback stored dataset actions open-loop instead of loading from simulation states.
    parser.add_argument(
        "--use-actions",
        action="store_true",
        help="use open-loop action playback instead of loading sim states",
    )

    # Playback stored dataset absolute actions open-loop instead of loading from simulation states.
    parser.add_argument(
        "--use-abs-actions",
        action="store_true",
        help="use open-loop action playback with absolute position actions instead of loading sim states",
    )

    # Whether to render playback to screen
    parser.add_argument(
        "--render",
        action="store_true",
        help="on-screen rendering",
    )

    # Dump a video of the dataset playback to the specified path
    parser.add_argument(
        "--video_path",
        type=str,
        default=None,
        help="(optional) render trajectories to this video file path",
    )

    # How often to write video frames during the playback
    parser.add_argument(
        "--video_skip",
        type=int,
        default=5,
        help="render frames to video every n steps",
    )

    # camera names to render, or image observations to use for writing to video
    parser.add_argument(
        "--render_image_names",
        type=str,
        nargs="+",
        default=[
            "robot0_agentview_left",
            "robot0_agentview_right",
            "robot0_eye_in_hand",
        ],
        help="(optional) camera name(s) / image observation(s) to use for rendering on-screen or to video. Default is"
        "None, which corresponds to a predefined camera for each env type",
    )

    # Only use the first frame of each episode
    parser.add_argument(
        "--first",
        action="store_true",
        help="use first frame of each episode",
    )

    parser.add_argument(
        "--extend_states",
        action="store_true",
        help="play last step of episodes for 50 extra frames",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="log additional information",
    )
    parser.add_argument(
        "--demos",
        nargs="+",  # Accepts one or more values
        type=str,   # Specify the type of each item in the list
        help="A list of items",
    )
    parser.add_argument(
        "--add_raw_states",
        action="store_true",
        help="create new dataset with raw states",
    )
    args = parser.parse_args()
    playback_dataset(args)
