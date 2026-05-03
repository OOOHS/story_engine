#!/bin/bash

# Description:
# 用于创建和配置story_engine环境

# Configuration
ENV_NAME="story_engine"
PYTHON_VERSION="3.12"
REQUIREMENTS_FILE="requirements.txt"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}Starting environment setup for $ENV_NAME...${NC}"

# Check if conda is installed
if ! command -v conda &> /dev/null; then
    echo -e "${RED}Error: conda is not installed or not in your PATH.${NC}"
    echo "Please install Anaconda or Miniconda first."
    exit 1
fi

# Initialize conda for shell interaction
eval "$(conda shell.bash hook)"

# Check if environment exists
if conda info --envs | grep -q "^$ENV_NAME "; then
    echo -e "${YELLOW}Environment '$ENV_NAME' already exists.${NC}"
    read -p "Do you want to recreate it? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Removing existing environment..."
        conda env remove -n $ENV_NAME -y
    else
        echo "Skipping environment creation."
    fi
fi

# Create environment if it doesn't exist (or was just removed)
if ! conda info --envs | grep -q "^$ENV_NAME "; then
    echo -e "${GREEN}Creating conda environment '$ENV_NAME' with Python $PYTHON_VERSION...${NC}"
    conda create -n $ENV_NAME python=$PYTHON_VERSION -y
else
    echo -e "${YELLOW}Using existing environment '$ENV_NAME'.${NC}"
fi

# Install requirements
if [ -f "$REQUIREMENTS_FILE" ]; then
    echo -e "${GREEN}Installing dependencies from $REQUIREMENTS_FILE...${NC}"
    
    # Use pip explicitly from the environment
    # Finding the pip path for the environment
    ENV_PIP="$(conda info --base)/envs/$ENV_NAME/bin/pip"
    
    # Fallback if path is different (e.g. on Windows or custom paths, though this is bash script)
    if [ ! -f "$ENV_PIP" ]; then
        # Try running via conda run
        conda run -n $ENV_NAME pip install -r $REQUIREMENTS_FILE
    else
        "$ENV_PIP" install -r $REQUIREMENTS_FILE
    fi
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}Dependencies installed successfully!${NC}"
    else
        echo -e "${RED}Failed to install dependencies.${NC}"
        exit 1
    fi
else
    echo -e "${RED}Error: $REQUIREMENTS_FILE not found!${NC}"
    exit 1
fi

echo
echo -e "${GREEN}Setup complete!${NC}"
echo -e "To activate the environment, run:"
echo -e "${YELLOW}conda activate $ENV_NAME${NC}"
