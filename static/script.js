document.addEventListener('DOMContentLoaded', () => {
    // --- DOM Element References ---
    const tabsContainer = document.getElementById('tabs');
    const contentContainer = document.getElementById('content-area');
    const loading = document.getElementById('loading');
    const blocker = document.getElementById('ps5-blocker');
    const searchBar = document.getElementById('search-bar');
    const shopTitleElement = document.getElementById('shop-title');

    // --- Pack Modal References ---
    const packModal = document.getElementById('pack-modal');
    const packModalTitle = document.getElementById('pack-modal-title');
    const packModalList = document.getElementById('pack-modal-list');
    const packModalCancel = document.getElementById('pack-modal-cancel');
    const packModalConfirm = document.getElementById('pack-modal-confirm');

    // --- State Management ---
    let pageState = {}; // Stores the current page for each category
    let currentItemCache = {}; // Caches the items for the current page of each category

    // --- Initialization ---

    // Main entry point
    async function initializeApp() {
        await loadSettings();
        checkUserAgent();
        setupModalListeners();
        setupSearchListener();
        setupControllerNavigation();
    }

    // Fetches and applies application settings
    async function loadSettings() {
        try {
            const response = await fetch('/api/settings');
            if (!response.ok) return;
            const settings = await response.json();
            if (settings.shop_title) {
                shopTitleElement.textContent = settings.shop_title;
                document.title = settings.shop_title;
            }
        } catch (error) {
            console.error('Could not load shop settings:', error);
        }
    }

    // Checks if the client is a PS5
    async function checkUserAgent() {
        try {
            const response = await fetch('/api/check_agent');
            if (!response.ok) throw new Error('Failed to verify user agent');
            const data = await response.json();
            if (data.is_ps5) {
                initializeScanner();
            } else {
                blocker.classList.remove('hidden');
                document.body.classList.add('no-scroll');
            }
        } catch (error) {
            console.error('Error during agent check:', error);
            blocker.classList.remove('hidden');
            document.body.classList.add('no-scroll');
        }
    }

    // --- Data Fetching and UI Building ---

    // Fetches the category list, processes it, and renders the UI.
    async function initializeScanner() {
        loading.classList.remove('hidden');
        tabsContainer.innerHTML = '';
        contentContainer.innerHTML = '';

        try {
            const response = await fetch(`/api/scan`);
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || `Error ${response.status}`);
            }
            const data = await response.json(); // Expects {"categories": [...]}
            const categories = data.categories || [];
            
            if (categories.length > 0) {
                renderTabsAndPanes(categories);
                activateTab(categories[0]); // Activate the first tab
            } else {
                contentContainer.innerHTML = '<p style="text-align: center;">No .pkg files found.</p>';
            }

        } catch (error) {
            console.error('Error while scanning:', error);
            contentContainer.innerHTML = `<p style="color: #ff5555; text-align: center;">Error: ${error.message}</p>`;
        } finally {
            loading.classList.add('hidden');
        }
    }
    
    // Creates the category tabs and their corresponding content panes.
    function renderTabsAndPanes(categories) {
        categories.forEach(categoryName => {
            pageState[categoryName] = 1; // Default to page 1
            currentItemCache[categoryName] = []; // Init item cache

            // Create Tab Button
            const tabButton = document.createElement('button');
            tabButton.textContent = categoryName;
            tabButton.dataset.category = categoryName;
            tabButton.addEventListener('click', () => activateTab(categoryName));
            tabsContainer.appendChild(tabButton);

            // Create Content Pane
            const tabPane = document.createElement('div');
            tabPane.id = `pane-${categoryName}`;
            tabPane.className = 'content-pane'; // Not hidden, 'active' class will control this
            tabPane.innerHTML = `
                <div class="gallery"></div>
                <div class="pagination-controls hidden">
                    <button class="btn-prev" disabled>Previous Page</button>
                    <span class="page-info">Page 1 / 1</span>
                    <button class="btn-next" disabled>Next Page</button>
                </div>
            `;
            contentContainer.appendChild(tabPane);
        });
    }

    // Handles tab switching logic
    function activateTab(categoryName) {
        tabsContainer.querySelectorAll('button').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.category === categoryName);
        });
        contentContainer.querySelectorAll('.content-pane').forEach(pane => {
            pane.classList.toggle('active', pane.id === `pane-${categoryName}`);
        });
        
        // Fetch data for this tab.
        // It will automatically fetch page 1 if not visited, or the stored page.
        fetchAndRenderPage(categoryName, pageState[categoryName]);
    }
    
    // Fetches a specific page of data for a specific category
    async function fetchAndRenderPage(categoryName, page) {
        loading.classList.remove('hidden'); // Show global loader
        const pane = document.getElementById(`pane-${categoryName}`);
        if (!pane) return; // Should not happen

        const gallery = pane.querySelector('.gallery');
        gallery.innerHTML = ''; // Clear gallery on page load

        try {
            const response = await fetch(`/api/items?category=${categoryName}&page=${page}`);
            if (!response.ok) throw new Error(`Error fetching items for ${categoryName}`);

            const data = await response.json(); // { items: [], current_page, total_pages }
            
            currentItemCache[categoryName] = data.items || [];
            pageState[categoryName] = data.current_page || 1;

            renderGallery(pane, currentItemCache[categoryName]);
            updatePagination(pane, categoryName, data.current_page, data.total_pages);

        } catch (error) {
            console.error('Error in fetchAndRenderPage:', error);
            gallery.innerHTML = `<p style="color: #ff5555; text-align: center;">Error: ${error.message}</p>`;
        } finally {
            loading.classList.add('hidden');
            searchBar.value = ''; // Clear search when changing pages
        }
    }

    // Renders a list of items into the gallery of a specific pane
    function renderGallery(pane, items) {
        const gallery = pane.querySelector('.gallery');
        gallery.innerHTML = '';

        if (items.length === 0) {
            gallery.innerHTML = '<p style="text-align: center;">No packages found in this section.</p>';
            return;
        }
        
        items.forEach(pkg => renderPkgCard(pkg, gallery));
    }

    // Creates and appends the HTML for a single package card.
    function renderPkgCard(pkg, container) {
        const cardButton = document.createElement('button');
        cardButton.className = 'pkg-card'; 
        
        if (pkg.is_pack) {
            cardButton.setAttribute('onClick', `showPackModal(${JSON.stringify(pkg.title)}, ${JSON.stringify(pkg.items)})`);
            cardButton.innerHTML = `
                <span class="pack-badge">PACK</span>
                <div class="img-container">
                    ${pkg.image_path ? 
                        `<img class="btn-img" loading="lazy" src="${pkg.image_path}?t=${new Date().getTime()}" alt="${pkg.title}">` : 
                        `<span class="no-image">No Image</span>`
                    }
                </div>
                <div class="info">
                    <p class="title">${pkg.title}</p>
                    <p class="file-size">${pkg.file_size_str} (${pkg.items.length} items)</p>
                </div>
            `;
        } else {
            const ip = window.location.hostname;
            const port = window.location.port;
            const pkgUrl = `http://${ip}${port ? ':' + port : ''}${pkg.install_url}`;
            
            cardButton.setAttribute('onClick', `installPkg('${pkgUrl}')`);
            cardButton.innerHTML = `
                <div class="img-container">
                    ${pkg.image_path ? 
                        `<img class="btn-img" loading="lazy" src="${pkg.image_path}?t=${new Date().getTime()}" alt="${pkg.title || pkg.content_id}">` : 
                        `<span class="no-image">No Image</span>`
                    }
                </div>
                <div class="info">
                    <p class="title">${pkg.title || pkg.content_id}</p>
                    <p class="file-size">${pkg.file_size_str}</p>
                </div>
            `;
        }
        container.appendChild(cardButton);
    }

    // --- UI Event Listeners ---

    // Updates the pagination button states and text for a specific pane
    function updatePagination(pane, categoryName, currentPage, totalPages) {
        const controls = pane.querySelector('.pagination-controls');
        const prevBtn = pane.querySelector('.btn-prev');
        const nextBtn = pane.querySelector('.btn-next');
        const pageInfo = pane.querySelector('.page-info');

        if (totalPages > 1) {
            pageInfo.textContent = `Page ${currentPage} / ${totalPages}`;
            prevBtn.disabled = (currentPage === 1);
            nextBtn.disabled = (currentPage === totalPages);
            
            // Set click handlers dynamically
            prevBtn.onclick = () => fetchAndRenderPage(categoryName, currentPage - 1);
            nextBtn.onclick = () => fetchAndRenderPage(categoryName, currentPage + 1);

            controls.classList.remove('hidden');
        } else {
            controls.classList.add('hidden');
        }
    }

    // Sets up the live search listener
    function setupSearchListener() {
        searchBar.addEventListener('input', (e) => {
            const searchTerm = e.target.value.toLowerCase().trim();
            const activeTab = tabsContainer.querySelector('button.active');
            if (!activeTab) return;

            const categoryName = activeTab.dataset.category;
            const pane = document.getElementById(`pane-${categoryName}`);
            const pagination = pane.querySelector('.pagination-controls');
            
            const itemsToFilter = currentItemCache[categoryName] || [];

            if (searchTerm) {
                const filtered = itemsToFilter.filter(pkg => {
                    const title = pkg.title || pkg.content_id || '';
                    return title.toLowerCase().includes(searchTerm);
                });
                renderGallery(pane, filtered);
                pagination.classList.add('hidden'); // Hide pagination during search
            } else {
                renderGallery(pane, itemsToFilter); // Restore full page
                pagination.classList.remove('hidden'); // Show pagination again
            }
        });
    }

    // Sets up L2/R2 navigation
    function setupControllerNavigation() {
        document.addEventListener('keydown', (event) => {
            if (searchBar === document.activeElement) return;

            const tabButtons = Array.from(tabsContainer.querySelectorAll('button'));
            if (tabButtons.length < 2) return;
            const currentIndex = tabButtons.findIndex(btn => btn.classList.contains('active'));
            let nextIndex = -1;

            if (event.keyCode === 118) { // L2
                event.preventDefault();
                nextIndex = (currentIndex - 1 + tabButtons.length) % tabButtons.length;
            } else if (event.keyCode === 119) { // R2
                event.preventDefault();
                nextIndex = (currentIndex + 1) % tabButtons.length;
            }

            if (nextIndex !== -1) {
                tabButtons[nextIndex].click();
            }
        });
    }

    // --- Pack Modal Functions (Unchanged) ---

    function formatCategoryType(type) {
        const types = {
            'gd': 'Game', 'gde': 'Game',
            'gp': 'Patch',
            'ac': 'DLC',
            'ap': 'App',
        };
        return types[type] || type || 'N/A';
    }

    window.showPackModal = (packTitle, items) => {
        packModalTitle.textContent = packTitle;
        packModalList.innerHTML = '';

        if (!items || items.length === 0) {
            packModalList.innerHTML = '<li>This pack is empty.</li>';
        } else {
            items.forEach(item => {
                const li = document.createElement('li');
                const type = formatCategoryType(item.category_type);
                li.innerHTML = `${item.title} <span class="pkg-type">${type}</span>`;
                packModalList.appendChild(li);
            });
        }
        
        packModalConfirm.onclick = () => installAllPkgs(items);
        
        packModal.classList.remove('hidden');
        document.body.classList.add('no-scroll');
    };

    function closePackModal() {
        packModal.classList.add('hidden');
        document.body.classList.remove('no-scroll');
    }

    function setupModalListeners() {
        packModalCancel.addEventListener('click', closePackModal);
        packModal.addEventListener('click', (e) => {
            if (e.target === packModal) {
                closePackModal();
            }
        });
    }

    function installAllPkgs(items) {
        if (!items || items.length === 0) {
            closePackModal();
            return;
        }

        showToast(`Sending ${items.length} packages to PS5...`, true);
        let failed = false;

        const installPromises = items.map(item => {
            const ip = window.location.hostname;
            const port = window.location.port;
            const pkgUrl = `http://${ip}${port ? ':' + port : ''}${item.install_url}`;
            
            return window.sendPkgToInstaller(pkgUrl)
                .catch(err => {
                    console.error("A pack item failed to send:", err);
                    failed = true;
                });
        });

        Promise.all(installPromises).then(() => {
            if (failed) {
                showToast(warningMessage, false);
            } else {
                showToast(`All ${items.length} downloads started on PS5!`, true);
            }
        });

        closePackModal();
    }
    
    // --- Start Application ---
    initializeApp();
});
