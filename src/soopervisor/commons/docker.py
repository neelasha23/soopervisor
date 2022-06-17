import tarfile
import os
from pathlib import Path

from ploomber.io._commander import CommanderStop
from ploomber.telemetry import telemetry

from soopervisor.commons import source, dependencies
from soopervisor.exceptions import ConfigurationError, MissingDockerfileError


def _validate_repository(repository):
    if repository == 'your-repository/name':
        raise ConfigurationError(
            f'Invalid repository {repository!r} '
            'in soopervisor.yaml, please add a valid value.')


def get_dependencies():
    """
    Fetch all dependency files and corresponding lock files
    mapped to corresponding task patterns, e.g., requirements.fit-*.txt
    and requirements.fit-*.lock.txt mapped to pattern fit-*.
    """

    requirement_files = dependencies.get_task_dependency_files(
        'requirements', 'txt')
    dependency_files = requirement_files if requirement_files \
        else dependencies.get_task_dependency_files('environment', 'yml')

    lock_paths = {
        task: paths['lock']
        for task, paths in dependency_files.items()
    }

    return dependency_files, lock_paths


def cp_ploomber_home(pkg_name):
    # Generate ploomber home
    home_path = Path(telemetry.get_home_dir(), 'stats')
    home_path = home_path.expanduser()

    if home_path.exists():
        target = Path('dist', f'{pkg_name}.tar.gz')
        archive = tarfile.open(target, "w:gz")
        archive.add(home_path, arcname='ploomber/stats')
        archive.close()


def modify_wildcard(entity):
    return entity.replace("*", "ploomber")

def build(e,
          cfg,
          env_name,
          until,
          entry_point,
          skip_tests=False,
          ignore_git=False):
    """Build a docker image

    Parameters
    ----------
    e : Commander
        Commander instance

    cfg
        Configuration

    env_name : str
        Target environment name

    until : str
        Stop after certain starge

    entry_point : str
        Entry point to use path/to/pipeline.yaml

    skip_tests : bool, default=False
        Skip image testing (check dag loading and File.client configuration)
    """

    if not Path(env_name, 'Dockerfile').is_file():
        raise MissingDockerfileError(env_name)

    # raise an error if the user didn't change the default value
    _validate_repository(cfg.repository)

    pkg_name, version = source.find_package_name_and_version()

    dependencies.check_lock_files_exist()

    dependency_files, lock_paths = get_dependencies()

    task_pattern_image_map = {}

    for task, lock_file in lock_paths.items():
        if Path(lock_file).exists():
            e.cp(lock_file)

        # Generate source distribution
        if Path('setup.py').exists():
            # raise error if multiple requirements files found
            for task_pattern in list(dependency_files.keys()):
                if task_pattern != 'default':
                    raise NotImplementedError(
                        "Multiple requirements.*.lock.txt or "
                        "environment.*.lock.yml files found along "
                        "with setup.py file. Please have either "
                        "of the two in the project root.")
            # .egg-info may cause issues if MANIFEST.in was recently updated
            e.rm('dist', 'build', Path('src', pkg_name,
                                       f'{pkg_name}.egg-info'))
            e.run('python',
                  '-m',
                  'build',
                  '--sdist',
                  description='Packaging code')

            # raise error if include is not None? and suggest to use MANIFEST.in
            # instead
        else:
            e.rm('dist')
            target = Path('dist', pkg_name)
            e.info('Packaging code')
            other_lock_files = [
                file for file in list(lock_paths.values()) if file != lock_file
            ]
            exclude = cfg.exclude
            if cfg.exclude and other_lock_files:
                exclude = cfg.exclude + other_lock_files
            elif not cfg.exclude and other_lock_files:
                exclude = other_lock_files

            rename_files = {lock_file: 'requirements.lock.txt'} if 'requirements' in lock_file \
                else {lock_file: 'environment.lock.yml'}
            source.copy(cmdr=e,
                        src='.',
                        dst=target,
                        include=cfg.include,
                        exclude=exclude,
                        ignore_git=ignore_git,
                        rename_files=rename_files)

            # need to remove this existing tar.gz file before next loop
            source.compress_dir(e, target, Path('dist', f'{pkg_name}.tar.gz'))

        e.cp('dist')

        e.cd(env_name)

        image_local = f'{pkg_name}:{version}-{task.replace("*","ploomber")}'

        # how to allow passing --no-cache?
        e.run('docker',
              'build',
              '.',
              '--tag',
              image_local,
              description='Building image')

        if not skip_tests:
            # test "ploomber status" in docker image
            e.run('docker',
                  'run',
                  image_local,
                  'ploomber',
                  'status',
                  '--entry-point',
                  entry_point,
                  description='Testing image',
                  error_message='Error while testing your docker image with',
                  hint=f'Use "docker run -it {image_local} /bin/bash" to '
                  'start an interactive session to debug your image')

            # check that the pipeline in the image has a configured File.client
            test_cmd = ('from ploomber.spec import DAGSpec; '
                        f'print("File" in DAGSpec("{entry_point}")'
                        '.to_dag().clients)')

            e.run('docker',
                  'run',
                  image_local,
                  'python',
                  '-c',
                  test_cmd,
                  description='Testing File client',
                  error_message='Missing File client',
                  hint=f'Run "docker run -it {image_local} /bin/bash" to '
                  'to debug your image. Ensure a File client is configured',
                  capture_output=True,
                  expected_output='True\n',
                  show_cmd=False)

        if until == 'build':
            raise CommanderStop('Done. Run "docker images" to see your image.')

        if cfg.repository:
            image_target = cfg.repository
            # Adding the latest tag if not a remote repo
            if ":" not in image_target:
                image_target = f'{image_target}:{version}-{modify_wildcard(task)}'

            e.run('docker',
                  'tag',
                  image_local,
                  image_target,
                  description='Tagging')
            e.run('docker', 'push', image_target, description='Pushing image')
        else:
            image_target = image_local

        if until == 'push':
            raise CommanderStop('Done. Image pushed to repository.')

        task_pattern_image_map[task] = image_target
        e.rm('dist')
        e.cd('..')

    e.info('Images generated : {}'.format(task_pattern_image_map))
    return pkg_name, task_pattern_image_map
