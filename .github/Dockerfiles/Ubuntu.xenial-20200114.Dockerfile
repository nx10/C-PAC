FROM ghcr.io/fcp-indi/c-pac_templates:latest as c-pac_templates
FROM nipreps/fmriprep:20.2.1 as fmriprep
FROM ubuntu:xenial-20200114 AS dcan-hcp

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y git

# add DCAN dependencies
RUN mkdir -p /opt/dcan-tools
# DCAN HCP code
RUN git clone -b 'v2.0.0' --single-branch --depth 1 https://github.com/DCAN-Labs/DCAN-HCP.git /opt/dcan-tools/pipeline

# using Ubuntu 16.04 LTS as parent image
FROM ubuntu:xenial-20200114
LABEL org.opencontainers.image.description "NOT INTENDED FOR USE OTHER THAN AS A STAGE IMAGE IN A MULTI-STAGE BUILD \
Ubuntu Xenial base image"
LABEL org.opencontainers.image.source https://github.com/FCP-INDI/C-PAC
ARG DEBIAN_FRONTEND=noninteractive

# create usergroup and user, set permissions, install curl
RUN groupadd -r c-pac && \
    useradd -r -g c-pac c-pac_user && \
    mkdir -p /home/c-pac_user/ && \
    chown -R c-pac_user:c-pac /home/c-pac_user && \
    chmod 777 / && \
    chmod ugo+w /etc/passwd && \
    apt-get update && \
    apt-get install -y apt-utils curl

RUN export XDG_CONFIG_HOME=/usr/bin && \
     curl https://raw.githubusercontent.com/nvm-sh/nvm/v0.35.2/install.sh | bash && \
     export NVM_DIR=/usr/bin/nvm && \
     [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh" && \
     [ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion" && \
     nvm install 12.12.0 && \
     nvm use 12.12.0 && \
     nvm alias default 12.12.0 && \
     npm install --global npm@^7 && \
     npm install -g bids-validator

ENV PATH=/usr/bin/nvm/versions/node/v12.12.0/bin:$PATH

# Install Ubuntu dependencies and utilities
RUN apt-get install -y \
      bc \
      build-essential \
      bzip2 \
      ca-certificates \
      cmake \
      git \
      graphviz \
      graphviz-dev \
      gsl-bin \
      libcanberra-gtk-module \
      libexpat1-dev \
      libgiftiio-dev \
      libglib2.0-dev \
      libglu1-mesa \
      libglu1-mesa-dev \
      libjpeg-progs \
      libgl1-mesa-dri \
      libglw1-mesa \
      libxml2 \
      libxml2-dev \
      libxext-dev \
      libxft2 \
      libxft-dev \
      libxi-dev \
      libxmu-headers \
      libxmu-dev \
      libxpm-dev \
      libxslt1-dev \
      locales \
      m4 \
      make \
      mesa-common-dev \
      mesa-utils \
      netpbm \
      openssh-client \
      pkg-config \
      rsync \
      tcsh \
      unzip \
      vim \
      xvfb \
      xauth \
      zlib1g-dev

# Install 16.04 dependencies
RUN apt-get install -y \
      dh-autoreconf \
      libgsl-dev \
      libmotif-dev \
      libtool \
      libx11-dev \
      libxext-dev \
      python3 \
      x11proto-xext-dev \
      x11proto-print-dev \
      xutils-dev

# Install libxp from third-party repository
RUN apt-get update && \
    apt-get install -y software-properties-common && \
    add-apt-repository --yes ppa:zeehio/libxp && \
    apt-get update && apt-get install libxp6 libxp-dev && \
    add-apt-repository --remove --yes ppa:zeehio/libxp && \
    apt-get update

# Installing and setting up wb_command
COPY --from=fmriprep /usr/local/etc/neurodebian.gpg /usr/local/etc/

RUN curl -sSL "http://neuro.debian.net/lists/$( lsb_release -c | cut -f2 ).us-ca.full" >> /etc/apt/sources.list.d/neurodebian.sources.list && \
    APT_KEY_DONT_WARN_ON_DANGEROUS_USAGE=1 apt-key add /usr/local/etc/neurodebian.gpg && \
    (apt-key adv --refresh-keys --keyserver hkp://ha.pool.sks-keyservers.net 0xA5D32F012649A5A9 || true)

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
                    connectome-workbench=1.3.2-2~nd16.04+1 && \
    apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Installing and setting up miniconda
RUN curl -sSLO https://repo.continuum.io/miniconda/Miniconda3-py37_4.12.0-Linux-x86_64.sh && \
    bash Miniconda3-py37_4.12.0-Linux-x86_64.sh -b -p /usr/local/miniconda && \
    rm Miniconda3-py37_4.12.0-Linux-x86_64.sh && chmod -R 777 /usr/local/miniconda

# Set CPATH for packages relying on compiled libs (e.g. indexed_gzip)
ENV PATH="/usr/local/miniconda/bin:$PATH" \
    CPATH="/usr/local/miniconda/include/:$CPATH" \
    LANG="C.UTF-8" \
    LC_ALL="C.UTF-8" \
    PYTHONNOUSERSITE=1

# install conda dependencies
RUN conda update conda -y && \
    conda install nomkl && \
    conda install -y  \
        blas \
        cython \
        matplotlib==2.2.2 \
        networkx==2.4 \
        nose==1.3.7 \
        numpy==1.15.4 \
        pandas==1.0.5 \
        scipy==1.6.3 \
        traits==4.6.0 \
        pip

# install torch
RUN pip install torch==1.2.0 torchvision==0.4.0 -f https://download.pytorch.org/whl/torch_stable.html

# install python dependencies
COPY requirements.txt /opt/requirements.txt
RUN pip install --upgrade setuptools
RUN pip install --upgrade pip
RUN pip install -r /opt/requirements.txt
RUN pip install xvfbwrapper

# install cpac templates
COPY --from=c-pac_templates /cpac_templates /cpac_templates
COPY --from=dcan-hcp /opt/dcan-tools/pipeline/global /opt/dcan-tools/pipeline/global

# Installing surface files for downsampling
COPY --from=c-pac_templates /opt/dcan-tools/pipeline/global/templates/standard_mesh_atlases/ /opt/dcan-tools/pipeline/global/templates/standard_mesh_atlases/
COPY --from=c-pac_templates /opt/dcan-tools/pipeline/global/templates/Greyordinates/ /opt/dcan-tools/pipeline/global/templates/Greyordinates/

RUN curl -s https://packagecloud.io/install/repositories/github/git-lfs/script.deb.sh | bash
RUN apt-get install git-lfs
RUN git lfs install

# Get atlases
COPY --from=ghcr.io/fcp-indi/c-pac/neuroparc:v1.0-human /ndmg_atlases /ndmg_atlases

ENTRYPOINT ["/bin/bash"]

# Link libraries for Singularity images
RUN ldconfig

RUN apt-get clean && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# set user
USER c-pac_user
